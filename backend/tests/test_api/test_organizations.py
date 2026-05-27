"""A2-002: 组织与账号 CRUD 验收测试"""

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


class TestOrganizationCRUD:
    @pytest.mark.anyio
    async def test_create_organization(self, client: AsyncClient):
        resp = await client.post("/api/v1/organizations", json={"name": "研发部"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "研发部"
        assert "id" in data

    @pytest.mark.anyio
    async def test_list_organizations(self, client: AsyncClient):
        await client.post("/api/v1/organizations", json={"name": "部门A"})
        resp = await client.get("/api/v1/organizations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestAccountCRUD:
    @pytest.mark.anyio
    async def test_create_account(self, client: AsyncClient):
        # 先创建租户+组织
        tenant_resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "Test",
                "admin_email": "a@b.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
        )
        assert tenant_resp.status_code == 201

        # 创建组织
        org_resp = await client.post("/api/v1/organizations", json={"name": "Test Org"})
        assert org_resp.status_code == 201
        org_id = org_resp.json()["id"]

        # 创建账号
        resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "user@test.com",
                "name": "Test User",
                "password": "Test1234",
                "organization_id": org_id,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "user@test.com"

    @pytest.mark.anyio
    async def test_list_accounts(self, client: AsyncClient):
        resp = await client.get("/api/v1/accounts")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_update_account(self, client: AsyncClient):
        resp = await client.patch(
            "/api/v1/accounts/00000000-0000-0000-0000-000000000000",
            json={"name": "New Name"},
        )
        assert resp.status_code == 404
