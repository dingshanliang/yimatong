"""W21: 区域品牌高级能力与白标测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
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
async def setup_regional(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "高级区域品牌",
            "admin_email": "adv@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    org_resp = await client.post(
        "/api/v1/regional/orgs",
        json={"name": "高级协会", "org_type": "association"},
        headers=headers,
    )
    org_id = org_resp.json()["id"]
    return tid, headers, org_id


class TestCodeRules:
    """W21-001: 统一码规则"""

    @pytest.mark.anyio
    async def test_create_code_rule(self, client: AsyncClient, setup_regional):
        tid, headers, org_id = setup_regional
        resp = await client.post(
            f"/api/v1/regional/orgs/{org_id}/code-rules",
            json={"rule_name": "标准码格式", "pattern": "BASE62_10", "prefix": "GN"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["rule_name"] == "标准码格式"

    @pytest.mark.anyio
    async def test_list_code_rules(self, client: AsyncClient, setup_regional):
        tid, headers, org_id = setup_regional
        await client.post(
            f"/api/v1/regional/orgs/{org_id}/code-rules",
            json={"rule_name": "列表测试", "pattern": "BASE62_10", "prefix": ""},
            headers=headers,
        )
        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/code-rules",
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestAdvancedDashboard:
    """W21-002: 高级汇总看板"""

    @pytest.mark.anyio
    async def test_member_comparison(self, client: AsyncClient, setup_regional):
        tid, headers, org_id = setup_regional
        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/advanced-dashboard",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "member_stats" in data


class TestWhitelabel:
    """W21-003: 白标配置"""

    @pytest.mark.anyio
    async def test_set_whitelabel(self, client: AsyncClient, setup_regional):
        tid, headers, org_id = setup_regional
        resp = await client.put(
            f"/api/v1/regional/orgs/{org_id}/whitelabel",
            json={"brand_name": "自定义品牌", "hide_yimatong": True, "primary_color": "#FF6600"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["brand_name"] == "自定义品牌"
        assert data["hide_yimatong"] is True

    @pytest.mark.anyio
    async def test_get_whitelabel(self, client: AsyncClient, setup_regional):
        tid, headers, org_id = setup_regional
        await client.put(
            f"/api/v1/regional/orgs/{org_id}/whitelabel",
            json={"brand_name": "获取测试", "hide_yimatong": False},
            headers=headers,
        )
        resp = await client.get(
            f"/api/v1/regional/orgs/{org_id}/whitelabel",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["brand_name"] == "获取测试"
