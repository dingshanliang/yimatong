import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_for_consumer
from app.main import app
from app.middleware.tenant import _is_member_notification_scan_token_path
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
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


def _token(tenant_id: uuid.UUID, consumer_id: uuid.UUID) -> str:
    return create_scan_token(
        public_id="MEMBER-CODE",
        ip_hash=compute_ip_hash("203.0.113.44"),
        tenant_id=str(tenant_id),
        consumer_id=str(consumer_id),
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )


def test_member_notification_scan_allowlist_is_exact() -> None:
    assert _is_member_notification_scan_token_path("/api/v1/consumers/membership/notifications")
    assert _is_member_notification_scan_token_path(
        "/api/v1/consumers/membership/notification-preferences/marketing-subscription"
    )
    assert not _is_member_notification_scan_token_path("/api/v1/consumers/membership/notifications/admin")
    assert not _is_member_notification_scan_token_path("/api/v1/member-notification-deliveries/x/attempt-result")


@pytest.mark.anyio
async def test_one_marketing_subscription_action_carries_consent_and_wechat_grant(client: AsyncClient) -> None:
    tenant_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    consent_id = uuid.uuid4()
    preference = SimpleNamespace(
        marketing_enabled=True,
        service_wechat_enabled=True,
        critical_sms_enabled=False,
        marketing_opted_in_at=datetime.now(UTC),
        marketing_opted_out_at=None,
    )
    update = AsyncMock(return_value=preference)
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.44"),
        patch(
            "app.api.v1.member_notifications.get_brand_membership_for_profile",
            new=AsyncMock(return_value={"membership_id": membership_id}),
        ),
        patch("app.api.v1.member_notifications.update_member_notification_preference", new=update),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
            headers={"Authorization": f"Bearer {_token(tenant_id, consumer_id)}"},
            json={
                "marketing_consent_id": str(consent_id),
                "template_code": "coupon_expiry",
            },
        )

    assert response.status_code == 200
    assert response.json()["marketing_enabled"] is True
    assert update.await_count == 1
    assert update.await_args.kwargs["marketing_consent_id"] == consent_id
    assert "authorization_ref" not in update.await_args.kwargs


@pytest.mark.anyio
async def test_marketing_subscription_rejects_client_fabricated_wechat_grant_facts(client: AsyncClient) -> None:
    tenant_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    with patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.44"):
        response = await client.post(
            "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
            headers={"Authorization": f"Bearer {_token(tenant_id, consumer_id)}"},
            json={
                "marketing_consent_id": str(uuid.uuid4()),
                "template_code": "coupon_expiry",
                "authorization_ref": "client-forged",
                "authorized_at": datetime.now(UTC).isoformat(),
                "expires_at": datetime.now(UTC).isoformat(),
            },
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_message_center_remains_readable_without_channel_authorization(client: AsyncClient) -> None:
    tenant_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    message = SimpleNamespace(
        id=uuid.uuid4(),
        notification_class="service",
        notification_type="order_refunded",
        title="订单退款已更新",
        body="退款结果已确认。",
        action_path="/member/orders/ORDER-A",
        facts={"status": "refunded"},
        occurred_at=datetime.now(UTC),
        read_at=None,
    )
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.44"),
        patch(
            "app.api.v1.member_notifications.get_brand_membership_for_profile",
            new=AsyncMock(return_value={"membership_id": membership_id}),
        ),
        patch("app.api.v1.member_notifications.list_member_notifications", new=AsyncMock(return_value=[message])),
    ):
        response = await client.get(
            "/api/v1/consumers/membership/notifications",
            headers={"Authorization": f"Bearer {_token(tenant_id, consumer_id)}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["notification_type"] == "order_refunded"
