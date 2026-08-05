import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db_with_bypass
from app.main import app
from app.models.tenant import Account, Role
from app.modules.initial_admin_activation import ActivationTicket
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import create_access_token


def _platform_headers() -> dict[str, str]:
    token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
        "Idempotency-Key": str(uuid.uuid4()),
    }


@pytest.mark.anyio
async def test_platform_creation_returns_activation_link_without_accepting_customer_password(db, monkeypatch):
    async def override_bypass():
        yield db

    async def fake_issue(self, *, tenant_id, operator_id, initial_admin_id=None):
        return ActivationTicket(
            initial_admin_id=initial_admin_id,
            url=f"https://example.test/activate?tenant={tenant_id}",
        )

    monkeypatch.setattr(
        "app.api.v1.platform.InitialAdminActivation.issue_or_reissue",
        fake_issue,
    )
    app.dependency_overrides[get_db_with_bypass] = override_bypass
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/platform/tenants",
                headers=_platform_headers(),
                json={
                    "name": "平台创建租户",
                    "plan": "starter",
                    "industry": "食品饮料",
                    "admin_name": "客户管理员",
                    "admin_email": "customer@example.com",
                },
            )
            tenant_id = response.json()["id"]
            tenant_list = await client.get(
                "/api/v1/platform/tenants",
                headers=_platform_headers(),
            )
            tenant_detail = await client.get(
                f"/api/v1/platform/tenants/{tenant_id}",
                headers=_platform_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["initial_admin_state"] == "pending_activation"
    assert payload["activation_url"].startswith("https://example.test/activate")
    assert payload["slug"]
    listed = next(item for item in tenant_list.json()["items"] if item["id"] == tenant_id)
    assert listed["initial_admin_state"] == "pending_activation"
    assert listed["activation_retryable"] is True
    assert tenant_detail.json()["activation_retryable"] is True

    account = await db.get(Account, uuid.UUID(payload["initial_admin_id"]))
    role = (
        await db.execute(select(Role).where(Role.tenant_id == account.tenant_id, Role.name == "admin"))
    ).scalar_one()
    await db.refresh(role, attribute_names=["permissions"])
    assert account.is_active is False
    assert {permission.code for permission in role.permissions} == set(WEB_ROLE_PERMISSIONS["admin"])


@pytest.mark.anyio
async def test_platform_creation_replays_same_result_for_same_idempotency_key(db, monkeypatch):
    async def override_bypass():
        yield db

    issued = 0

    async def fake_issue(self, *, tenant_id, operator_id, initial_admin_id=None):
        nonlocal issued
        issued += 1
        return ActivationTicket(
            initial_admin_id=initial_admin_id, url=f"https://example.test/activate?tenant={tenant_id}"
        )

    monkeypatch.setattr("app.api.v1.platform.InitialAdminActivation.issue_or_reissue", fake_issue)
    app.dependency_overrides[get_db_with_bypass] = override_bypass
    headers = _platform_headers()
    body = {
        "name": "幂等创建租户",
        "plan": "starter",
        "admin_name": "客户管理员",
        "admin_email": "idempotent@example.com",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/v1/platform/tenants", headers=headers, json=body)
            replay = await client.post("/api/v1/platform/tenants", headers=headers, json=body)
            conflict = await client.post(
                "/api/v1/platform/tenants",
                headers=headers,
                json={**body, "name": "另一个租户"},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["initial_admin_id"] == first.json()["initial_admin_id"]
    assert replay.json()["activation_url"] is None
    assert replay.json()["activation_retryable"] is True
    assert issued == 1
    assert conflict.status_code == 409
