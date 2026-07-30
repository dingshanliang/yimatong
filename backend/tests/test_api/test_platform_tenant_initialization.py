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
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


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
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["initial_admin_state"] == "pending_activation"
    assert payload["activation_url"].startswith("https://example.test/activate")
    assert payload["slug"]

    account = await db.get(Account, uuid.UUID(payload["initial_admin_id"]))
    role = (
        await db.execute(select(Role).where(Role.tenant_id == account.tenant_id, Role.name == "admin"))
    ).scalar_one()
    await db.refresh(role, attribute_names=["permissions"])
    assert account.is_active is False
    assert {permission.code for permission in role.permissions} == set(WEB_ROLE_PERMISSIONS["admin"])
