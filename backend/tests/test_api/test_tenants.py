"""A2-001: 租户 CRUD API 验收测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers(tenant_id: str) -> dict:
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sample_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "测试租户",
            "slug": "test-tenant",
            "plan": "free",
            "admin_email": "admin@test.com",
            "admin_name": "管理员",
            "admin_password": "Test1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code == 201
    return resp.json()


class TestCreateTenant:
    @pytest.mark.anyio
    async def test_create_tenant(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "新租户",
                "admin_email": "admin@new.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "新租户"
        assert data["slug"]
        assert data["status"] == "active"
        assert data["plan"] == "free"

    @pytest.mark.anyio
    async def test_create_tenant_with_custom_slug(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "My Company",
                "slug": "my-company",
                "admin_email": "admin@myco.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-company"

    @pytest.mark.anyio
    async def test_create_tenant_auto_slug_from_name(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "Hello World Company",
                "admin_email": "admin@hw.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201
        assert "hello" in resp.json()["slug"]


class TestGetTenant:
    @pytest.mark.anyio
    async def test_get_existing_tenant(self, client: AsyncClient, sample_tenant):
        resp = await client.get(
            f"/api/v1/tenants/{sample_tenant['id']}",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "测试租户"

    @pytest.mark.anyio
    async def test_get_nonexistent_tenant(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/tenants/00000000-0000-0000-0000-000000000000",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 404


class TestUpdateTenant:
    @pytest.mark.anyio
    async def test_update_tenant_name(self, client: AsyncClient, sample_tenant):
        resp = await client.patch(
            f"/api/v1/tenants/{sample_tenant['id']}",
            json={"name": "更新后的名称"},
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "更新后的名称"

    @pytest.mark.anyio
    async def test_update_nonexistent_tenant(self, client: AsyncClient):
        resp = await client.patch(
            "/api/v1/tenants/00000000-0000-0000-0000-000000000000",
            json={"name": "不存在"},
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 404


class TestDeleteTenant:
    @pytest.mark.anyio
    async def test_soft_delete_tenant(self, client: AsyncClient, sample_tenant):
        resp = await client.delete(
            f"/api/v1/tenants/{sample_tenant['id']}",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 204

        resp = await client.get(
            f"/api/v1/tenants/{sample_tenant['id']}",
            headers=_platform_admin_headers(),
        )
        assert resp.json()["status"] == "terminated"


class TestTenantSelfBrandProfile:
    """租户自助 brand_profile 五槽位（ADR-0001：logo_url 并入 brand_profile）"""

    async def test_update_brand_profile_with_logo_url(self, client: AsyncClient, sample_tenant):
        """PATCH /me 写入完整五槽位（含 logo_url），GET /me 读回一致"""
        tid = sample_tenant["id"]
        profile = {
            "primary_color": "#1F7A4D",
            "radius_preset": "md",
            "background_preset": "tinted",
            "hide_yimatong_brand": True,
            "logo_url": "https://cdn.example.com/t/logo.png",
        }
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"brand_profile": profile},
            headers=_auth_headers(tid),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["brand_profile"] == profile

        # 读回一致
        resp = await client.get("/api/v1/tenants/me", headers=_auth_headers(tid))
        assert resp.json()["brand_profile"] == profile

    async def test_update_brand_profile_rejects_bad_logo_url(self, client: AsyncClient, sample_tenant):
        """非法 logo_url 被 422 拒绝"""
        tid = sample_tenant["id"]
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"brand_profile": {"logo_url": "javascript:alert(1)"}},
            headers=_auth_headers(tid),
        )
        assert resp.status_code == 422

    async def test_update_brand_profile_rejects_unknown_slot(self, client: AsyncClient, sample_tenant):
        """未知槽位（如 custom_css）被拒绝，保护受控槽位边界"""
        tid = sample_tenant["id"]
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"brand_profile": {"custom_css": "body{}"}},
            headers=_auth_headers(tid),
        )
        assert resp.status_code == 422
