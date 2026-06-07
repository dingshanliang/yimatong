"""A2-002: 组织与账号 CRUD 验收测试"""

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
async def tenant_with_auth(client: AsyncClient):
    """创建租户并返回 (tenant_id, auth_headers)"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "Test",
            "admin_email": "a@b.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code == 201
    tenant_id = resp.json()["id"]
    return tenant_id, _auth_headers(tenant_id)


class TestOrganizationCRUD:
    @pytest.mark.anyio
    async def test_create_organization(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        resp = await client.post("/api/v1/organizations", json={"name": "研发部"}, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "研发部"
        assert data["tenant_id"] == tenant_id

    @pytest.mark.anyio
    async def test_list_organizations(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        await client.post("/api/v1/organizations", json={"name": "部门A"}, headers=headers)
        resp = await client.get("/api/v1/organizations", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)


class TestAccountCRUD:
    @pytest.mark.anyio
    async def test_create_account(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth

        # 创建组织
        org_resp = await client.post("/api/v1/organizations", json={"name": "Test Org"}, headers=headers)
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
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "user@test.com"

    @pytest.mark.anyio
    async def test_create_account_can_generate_initial_password(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "运营部"}, headers=headers)
        org_id = org_resp.json()["id"]

        resp = await client.post(
            "/api/v1/accounts",
            json={"email": "ops@test.com", "name": "运营账号", "organization_id": org_id},
            headers=headers,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["organization_name"] == "运营部"
        assert len(data["initial_password"]) >= 12

    @pytest.mark.anyio
    async def test_lists_include_business_context(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "销售部"}, headers=headers)
        org_id = org_resp.json()["id"]
        await client.post(
            "/api/v1/accounts",
            json={"email": "sales@test.com", "name": "销售账号", "organization_id": org_id},
            headers=headers,
        )

        orgs_resp = await client.get("/api/v1/organizations", headers=headers)
        accounts_resp = await client.get("/api/v1/accounts", headers=headers)

        assert orgs_resp.status_code == 200
        org_data = orgs_resp.json()
        org_item = next(item for item in org_data["items"] if item["id"] == org_id)
        assert org_item["account_count"] == 1
        assert accounts_resp.status_code == 200
        account_data = accounts_resp.json()
        account_item = next(item for item in account_data["items"] if item["email"] == "sales@test.com")
        assert account_item["organization_name"] == "销售部"
        assert "initial_password" not in account_item

    @pytest.mark.anyio
    async def test_list_accounts(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.get("/api/v1/accounts", headers=headers)
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_update_account(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.patch(
            "/api/v1/accounts/00000000-0000-0000-0000-000000000000",
            json={"name": "New Name"},
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_pagination_params(self, client: AsyncClient, tenant_with_auth):
        """分页参数正确传递并返回分页结构"""
        _, headers = tenant_with_auth
        # 创建 3 个组织
        for name in ["部门X", "部门Y", "部门Z"]:
            await client.post("/api/v1/organizations", json={"name": name}, headers=headers)

        resp = await client.get("/api/v1/organizations?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["items"]) <= 2
        assert data["total"] >= 3  # 至少有 3 个新创建的 + 默认的

    @pytest.mark.anyio
    async def test_search_by_keyword(self, client: AsyncClient, tenant_with_auth):
        """关键词搜索过滤结果"""
        _, headers = tenant_with_auth
        await client.post("/api/v1/organizations", json={"name": "独一无二部门"}, headers=headers)
        await client.post("/api/v1/organizations", json={"name": "普通部门"}, headers=headers)

        resp = await client.get("/api/v1/organizations?q=独一无二", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("独一无二" in item["name"] for item in data["items"])
