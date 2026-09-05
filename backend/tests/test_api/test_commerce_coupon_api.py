import json
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_for_consumer
from app.main import app
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db_for_consumer] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {
        "X-Commerce-Credential-Id": str(uuid.uuid4()),
        "X-Commerce-Timestamp": "1787443200",
        "X-Commerce-Signature": "a" * 64,
    }


def _authority():
    tenant_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    return (
        SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            connection_id=connection_id,
            direction="commerce_to_yimatong",
        ),
        SimpleNamespace(id=connection_id, tenant_id=tenant_id, status="active"),
    )


@pytest.mark.anyio
async def test_signed_eligible_coupon_endpoint_uses_exact_body_and_returns_storefront_contract(client: AsyncClient):
    credential, connection = _authority()
    verify = Mock()
    eligible = AsyncMock(
        return_value=[
            {
                "coupon_ref": str(uuid.uuid4()),
                "display_name": "复购立减 10 元",
                "amount_fen": 1000,
                "minimum_spend_fen": 5000,
                "valid_until": "2026-09-23T00:00:00Z",
            }
        ]
    )
    body = {
        "member_ref": "cmr_member",
        "cart_ref": "cart_001",
        "goods_subtotal_fen": 6000,
        "line_items": [
            {"product_ref": "prod_1", "sku_ref": "sku_1", "quantity": 1, "amount_fen": 6000}
        ],
    }
    with (
        patch(
            "app.api.v1.commerce_integrations.resolve_commerce_credential",
            new=AsyncMock(return_value=(credential, connection)),
        ),
        patch("app.api.v1.commerce_integrations.verify_commerce_signature", new=verify),
        patch("app.api.v1.commerce_integrations.list_eligible_commerce_coupons", new=eligible),
    ):
        response = await client.post("/api/v1/commerce/coupons/eligible", headers=_headers(), json=body)

    assert response.status_code == 200
    assert response.json()["coupons"][0]["amount_fen"] == 1000
    signed = verify.call_args.kwargs
    assert signed["path"] == "/api/v1/commerce/coupons/eligible"
    assert json.loads(signed["body"]) == body
    assert eligible.await_args.kwargs["member_ref"] == "cmr_member"


@pytest.mark.anyio
async def test_signed_coupon_transition_requires_refund_scope_and_returns_authoritative_state(client: AsyncClient):
    credential, connection = _authority()
    coupon_id = uuid.uuid4()
    transition = AsyncMock(
        return_value=SimpleNamespace(
            id=coupon_id,
            status="used",
            reserved_discount_minor=None,
            reservation_expires_at=None,
        )
    )
    body = {
        "coupon_ref": str(coupon_id),
        "member_ref": "cmr_member",
        "order_id": "order_001",
        "amount_fen": 1000,
        "action": "reverse",
        "idempotency_key": "refund-order-001",
        "full_refund": False,
    }
    with (
        patch(
            "app.api.v1.commerce_integrations.resolve_commerce_credential",
            new=AsyncMock(return_value=(credential, connection)),
        ),
        patch("app.api.v1.commerce_integrations.verify_commerce_signature"),
        patch("app.api.v1.commerce_integrations.transition_commerce_coupon", new=transition),
    ):
        response = await client.post("/api/v1/commerce/coupons/transitions", headers=_headers(), json=body)

    assert response.status_code == 200
    assert response.json() == {
        "coupon_ref": str(coupon_id),
        "status": "used",
        "discount_amount_fen": 1000,
        "reservation_expires_at": None,
    }
    assert transition.await_args.kwargs["full_refund"] is False
