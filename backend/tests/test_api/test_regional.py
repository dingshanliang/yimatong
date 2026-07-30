"""W15: 区域品牌/协会基础版测试"""

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


@pytest.fixture
async def setup_regional(client: AsyncClient):
    """创建区域品牌组织和两个成员企业"""
    # 创建上级组织租户
    org_resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "赣南脐橙协会",
            "admin_email": "regional@test.com",
            "admin_name": "RegionalAdmin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    org_tid = org_resp.json()["id"]
    org_token = create_access_token(org_tid, "00000000-0000-0000-0000-000000000001", "admin")
    org_headers = {"Authorization": f"Bearer {org_token}"}

    # 创建成员企业1
    m1_resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "成员企业A",
            "admin_email": "member1@test.com",
            "admin_name": "Member1",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    m1_tid = m1_resp.json()["id"]

    # 创建成员企业2
    m2_resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "成员企业B",
            "admin_email": "member2@test.com",
            "admin_name": "Member2",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    m2_tid = m2_resp.json()["id"]

    return org_tid, org_headers, m1_tid, m2_tid


class TestRegionalOrgCRUD:
    """W15-001: RegionalOrg 模型与 CRUD"""

    @pytest.mark.anyio
    async def test_create_regional_org(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, _, _ = setup_regional
        resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "赣南脐橙协会", "org_type": "association"},
            headers=org_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "赣南脐橙协会"
        assert data["org_type"] == "association"

    @pytest.mark.anyio
    async def test_list_regional_orgs(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, _, _ = setup_regional
        await client.post(
            "/api/v1/regional/orgs",
            json={"name": "测试协会", "org_type": "brand"},
            headers=org_headers,
        )
        resp = await client.get("/api/v1/regional/orgs", headers=org_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.anyio
    async def test_list_regional_orgs_includes_operational_summary(self, client: AsyncClient, setup_regional):
        _, org_headers, m1_tid, _ = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "闭环协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m1_tid, "member_name": "成员企业A"},
            headers=org_headers,
        )
        await client.post(
            f"/api/v1/regional/orgs/{org_id}/templates",
            json={"name": "统一扫码页", "config": {"layout": "standard"}},
            headers=org_headers,
        )

        resp = await client.get("/api/v1/regional/orgs", headers=org_headers)
        assert resp.status_code == 200
        item = next(item for item in resp.json() if item["id"] == org_id)
        assert item["org_type_label"] == "协会组织"
        assert item["member_count"] == 1
        assert item["active_member_count"] == 1
        assert item["template_count"] == 1
        assert item["next_action"] == "配置统一活动"


class TestMemberManagement:
    """W15-002: 成员企业关系管理"""

    @pytest.mark.anyio
    async def test_add_member(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, m1_tid, _ = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "测试协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m1_tid, "member_name": "成员企业A"},
            headers=org_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["tenant_id"] == m1_tid

    @pytest.mark.anyio
    async def test_list_members(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, m1_tid, m2_tid = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "多成员协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m1_tid, "member_name": "成员A"},
            headers=org_headers,
        )
        await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m2_tid, "member_name": "成员B"},
            headers=org_headers,
        )

        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/members",
            headers=org_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 2


class TestSharedTemplate:
    """W15-003: 统一模板管理"""

    @pytest.mark.anyio
    async def test_create_shared_template(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, _, _ = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "模板测试协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/regional/orgs/{org_id}/templates",
            json={"name": "统一包装页", "config": {"layout": "standard"}},
            headers=org_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "统一包装页"

    @pytest.mark.anyio
    async def test_list_shared_templates(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, _, _ = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "模板列表协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        await client.post(
            f"/api/v1/regional/orgs/{org_id}/templates",
            json={"name": "模板A", "config": {}},
            headers=org_headers,
        )

        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/templates",
            headers=org_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestAuthorizedProducts:
    """W15-004: 授权产品管理"""

    @pytest.mark.anyio
    async def test_authorize_product(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, m1_tid, _ = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "产品授权协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        # 在上级组织创建品牌和产品
        brand_resp = await client.post(
            "/api/v1/brands",
            json={"name": "区域品牌"},
            headers=org_headers,
        )
        brand_id = brand_resp.json()["id"]

        product_resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "授权产品", "category": "水果"},
            headers=org_headers,
        )
        product_id = product_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/regional/orgs/{org_id}/products",
            json={"product_id": product_id, "tenant_id": m1_tid},
            headers=org_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["product_id"] == product_id


class TestRegionalDashboard:
    """W15-005: 区域品牌汇总看板"""

    @pytest.mark.anyio
    async def test_regional_dashboard(self, client: AsyncClient, setup_regional):
        org_tid, org_headers, m1_tid, m2_tid = setup_regional
        org_resp = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "看板测试协会", "org_type": "association"},
            headers=org_headers,
        )
        org_id = org_resp.json()["id"]

        await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m1_tid, "member_name": "成员A"},
            headers=org_headers,
        )
        await client.post(
            f"/api/v1/regional/orgs/{org_id}/members",
            json={"tenant_id": m2_tid, "member_name": "成员B"},
            headers=org_headers,
        )

        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/dashboard",
            headers=org_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["member_count"] >= 2
        assert "total_scans" in data
