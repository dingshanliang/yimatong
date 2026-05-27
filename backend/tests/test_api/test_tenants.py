"""A2-001: 租户 CRUD API 验收测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


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
        )
        assert resp.status_code == 201
        assert "hello" in resp.json()["slug"]


class TestGetTenant:
    @pytest.mark.anyio
    async def test_get_existing_tenant(self, client: AsyncClient, sample_tenant):
        resp = await client.get(f"/api/v1/tenants/{sample_tenant['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "测试租户"

    @pytest.mark.anyio
    async def test_get_nonexistent_tenant(self, client: AsyncClient):
        resp = await client.get("/api/v1/tenants/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestUpdateTenant:
    @pytest.mark.anyio
    async def test_update_tenant_name(self, client: AsyncClient, sample_tenant):
        resp = await client.patch(
            f"/api/v1/tenants/{sample_tenant['id']}",
            json={"name": "更新后的名称"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "更新后的名称"

    @pytest.mark.anyio
    async def test_update_nonexistent_tenant(self, client: AsyncClient):
        resp = await client.patch(
            "/api/v1/tenants/00000000-0000-0000-0000-000000000000",
            json={"name": "不存在"},
        )
        assert resp.status_code == 404


class TestDeleteTenant:
    @pytest.mark.anyio
    async def test_soft_delete_tenant(self, client: AsyncClient, sample_tenant):
        resp = await client.delete(f"/api/v1/tenants/{sample_tenant['id']}")
        assert resp.status_code == 204

        resp = await client.get(f"/api/v1/tenants/{sample_tenant['id']}")
        assert resp.json()["status"] == "terminated"
