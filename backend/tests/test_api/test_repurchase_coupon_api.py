import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_for_consumer
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.main import app
from app.middleware.tenant import _is_member_coupon_scan_token_path
from app.services.channel_access import require_store_portal_principal
from app.services.repurchase_coupon import issue_store_redemption_token
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def test_member_coupon_scan_token_allowlist_is_exact() -> None:
    coupon_id = "d3f42bdb-0362-47b3-830f-9449d062d26f"
    assert _is_member_coupon_scan_token_path("/api/v1/consumers/membership/coupons") is True
    assert _is_member_coupon_scan_token_path(f"/api/v1/consumers/membership/coupons/{coupon_id}/store-token") is True
    assert _is_member_coupon_scan_token_path("/api/v1/consumers/membership/coupons/admin") is False
    assert (
        _is_member_coupon_scan_token_path(f"/api/v1/consumers/membership/coupons/{coupon_id}/store-token/evil") is False
    )


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_for_consumer] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _scan_token(tenant_id: uuid.UUID, consumer_id: uuid.UUID, ip: str = "203.0.113.44") -> str:
    return create_scan_token(
        public_id="MEMBER-CODE",
        ip_hash=compute_ip_hash(ip),
        tenant_id=str(tenant_id),
        consumer_id=str(consumer_id),
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )


def _coupon(membership_id: uuid.UUID):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        coupon_number="RCP-0123456789ABCDEF",
        membership_id=membership_id,
        rule_version_id=uuid.uuid4(),
        status="available",
        valid_from=now,
        valid_until=now + timedelta(days=30),
        reserved_order_ref=None,
        reservation_expires_at=None,
        used_order_ref=None,
        used_store_id=None,
    )


def _rule():
    return SimpleNamespace(
        name="复购立减 10 元",
        amount_minor=1000,
        minimum_spend_minor=5000,
        product_scope="all",
        eligible_product_refs=[],
        channel_scope="both",
    )


@pytest.mark.anyio
async def test_recovered_member_wallet_returns_authoritative_coupon_state(client: AsyncClient):
    tenant_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    coupon = _coupon(membership_id)
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.44"),
        patch(
            "app.api.v1.repurchase_coupons.get_brand_membership_for_profile",
            new=AsyncMock(return_value={"membership_id": membership_id}),
        ),
        patch("app.api.v1.repurchase_coupons.list_member_wallet", new=AsyncMock(return_value=[coupon])),
        patch("app.api.v1.repurchase_coupons.get_coupon_rule", new=AsyncMock(return_value=_rule())),
    ):
        response = await client.get(
            "/api/v1/consumers/membership/coupons",
            headers={"Authorization": f"Bearer {_scan_token(tenant_id, consumer_id)}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "available"
    assert response.json()[0]["amount_minor"] == 1000
    assert response.json()[0]["membership_id"] == str(membership_id)


@pytest.mark.anyio
async def test_store_redeem_uses_authenticated_accounts_own_store_scope(client: AsyncClient):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    coupon = _coupon(membership_id)
    store_id = uuid.uuid4()
    token = issue_store_redemption_token(tenant_id, membership_id, coupon.id)
    redeemed = {**coupon.__dict__, "status": "used", "used_store_id": store_id}
    redeem = AsyncMock(return_value=SimpleNamespace(**redeemed))

    app.dependency_overrides[require_store_portal_principal] = lambda: None
    app.dependency_overrides[get_current_tenant] = lambda: tenant_id
    app.dependency_overrides[get_current_account_id] = lambda: account_id
    with (
        patch(
            "app.api.v1.repurchase_coupons.get_account_scope",
            new=AsyncMock(return_value=SimpleNamespace(store_id=store_id)),
        ),
        patch("app.api.v1.repurchase_coupons.redeem_member_coupon_at_store", new=redeem),
        patch("app.api.v1.repurchase_coupons.get_coupon_rule", new=AsyncMock(return_value=_rule())),
    ):
        response = await client.post(
            "/api/v1/repurchase-coupons/store/redeem",
            headers={"Authorization": f"Bearer {create_access_token(str(tenant_id), str(account_id), 'store_guide')}"},
            json={"redemption_token": token},
        )

    assert response.status_code == 200
    assert response.json()["used_store_id"] == str(store_id)
    assert redeem.await_args.kwargs["store_id"] == store_id
    assert redeem.await_args.kwargs["actor_id"] == account_id
    assert redeem.await_args.kwargs["idempotency_key"].startswith("store-redemption:")
