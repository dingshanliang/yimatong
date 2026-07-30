"""邀请码系统测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestInviteCodeCRUD:
    @pytest.mark.anyio
    async def test_create_invite_code(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 5, "expires_in_days": 7},
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["code"]
        assert data["tenant_type"] == "brand"
        assert data["max_uses"] == 5
        assert data["used_count"] == 0
        assert data["status"] == "active"

    @pytest.mark.anyio
    async def test_list_invite_codes(self, client: AsyncClient):
        await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "agency", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        resp = await client.get(
            "/api/v1/invite-codes",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    @pytest.mark.anyio
    async def test_deactivate_invite_code(self, client: AsyncClient):
        create_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/invite-codes/{code_id}/status?active=false",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"


class TestInviteCodeRegistration:
    @pytest.mark.anyio
    async def test_register_with_valid_invite_code(self, client: AsyncClient):
        # Create invite code
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]

        # Register with invite code
        resp = await client.post(
            "/api/v1/invite-codes/register",
            json={
                "invite_code": code,
                "name": " invited Tenant",
                "admin_email": "invited@test.com",
                "admin_name": "Invited",
                "admin_password": "Pass1234",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["tenant_id"]
        assert "注册成功" in data["message"]

    @pytest.mark.anyio
    async def test_register_with_invalid_invite_code(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/invite-codes/register",
            json={
                "invite_code": "INVALIDCODE",
                "name": "Bad Tenant",
                "admin_email": "bad@test.com",
                "admin_name": "Bad",
                "admin_password": "Pass1234",
            },
        )
        assert resp.status_code == 400
        assert "Invalid" in resp.json()["detail"] or "邀请码" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_invite_code_depletes_after_use(self, client: AsyncClient):
        # Create invite code with max_uses=1
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]

        # First registration succeeds
        resp1 = await client.post(
            "/api/v1/invite-codes/register",
            json={
                "invite_code": code,
                "name": "First Tenant",
                "admin_email": "first@test.com",
                "admin_name": "First",
                "admin_password": "Pass1234",
            },
        )
        assert resp1.status_code == 201, resp1.text

        # Second registration fails (code depleted)
        resp2 = await client.post(
            "/api/v1/invite-codes/register",
            json={
                "invite_code": code,
                "name": "Second Tenant",
                "admin_email": "second@test.com",
                "admin_name": "Second",
                "admin_password": "Pass1234",
            },
        )
        assert resp2.status_code == 400


class TestInviteCodeSecurity:
    @pytest.mark.anyio
    async def test_create_invite_code_requires_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Authorization": f"Bearer {regular_token}"}
        resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand"},
            headers=headers,
        )
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_list_invite_codes_requires_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Authorization": f"Bearer {regular_token}"}
        resp = await client.get("/api/v1/invite-codes", headers=headers)
        assert resp.status_code == 403
