"""Risk notification API mutation boundaries."""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict[str, str]:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with TestSessionLocal() as session:

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
            yield value
        app.dependency_overrides.clear()


async def _create_tenant(client: AsyncClient) -> str:
    unique = uuid.uuid4().hex
    response = await client.post(
        "/api/v1/tenants",
        json={
            "name": f"风控通知边界租户-{unique}",
            "admin_email": f"risk-notification-{unique}@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert response.status_code == 201, response.text
    tenant_id = response.json()["id"]
    enabled = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"enabled_features": {"risk_module": True}},
        headers=_platform_admin_headers(),
    )
    assert enabled.status_code == 200, enabled.text
    return tenant_id


def _headers(
    tenant_id: str,
    role: str,
    *,
    idempotency_key: str | None = "notification-idem-1",
) -> dict[str, str]:
    token = create_access_token(
        tenant_id,
        "00000000-0000-0000-0000-000000000102",
        role,
        "brand",
        extra={"sid": "00000000-0000-0000-0000-000000000103"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


@pytest.mark.anyio
async def test_mark_one_and_all_forward_stable_idempotency_without_direct_dml(client, monkeypatch):
    tenant_id = await _create_tenant(client)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    notification_id = uuid.uuid4()
    mark_one = AsyncMock(
        return_value={"notification_id": notification_id, "read": True, "changed": True, "replayed": False}
    )
    mark_all = AsyncMock(return_value={"updated_count": 3, "replayed": False})
    monkeypatch.setattr("app.api.v1.risk_notifications.mark_notification_read", mark_one)
    monkeypatch.setattr("app.api.v1.risk_notifications.mark_all_notifications_read", mark_all)

    one = await client.post(
        f"/api/v1/risk-notifications/{notification_id}/read",
        headers=_headers(tenant_id, "admin", idempotency_key="notification-one-idem"),
    )
    all_read = await client.post(
        "/api/v1/risk-notifications/mark-all-read",
        headers=_headers(tenant_id, "operator", idempotency_key="notification-all-idem"),
    )

    assert one.status_code == 200 and one.json() == {"id": str(notification_id), "read": True}
    assert all_read.status_code == 200 and all_read.json() == {"updated": 3}
    assert mark_one.await_args.kwargs["idempotency_key"] == "notification-one-idem"
    assert mark_all.await_args.kwargs["idempotency_key"] == "notification-all-idem"


@pytest.mark.anyio
async def test_notification_mutations_require_idempotency_header_before_authority(client, monkeypatch):
    tenant_id = await _create_tenant(client)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    notification_id = uuid.uuid4()
    authority = AsyncMock()
    monkeypatch.setattr("app.api.v1.risk_notifications.mark_notification_read", authority)

    response = await client.post(
        f"/api/v1/risk-notifications/{notification_id}/read",
        headers=_headers(tenant_id, "admin", idempotency_key=None),
    )

    assert response.status_code == 422
    authority.assert_not_awaited()


@pytest.mark.anyio
async def test_viewer_is_denied_before_notification_authority(client, monkeypatch):
    tenant_id = await _create_tenant(client)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    authority = AsyncMock()
    monkeypatch.setattr("app.api.v1.risk_notifications.mark_all_notifications_read", authority)

    response = await client.post("/api/v1/risk-notifications/mark-all-read", headers=_headers(tenant_id, "viewer"))

    assert response.status_code == 403
    authority.assert_not_awaited()
