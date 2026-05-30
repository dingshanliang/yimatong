"""W17: AI 资料识别与文案生成测试"""

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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "AI测试租户",
            "admin_email": "ai@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestCopywriting:
    """W17-001: AI 文案生成"""

    @pytest.mark.anyio
    async def test_generate_brand_story(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/ai/copywriting",
            json={
                "type": "brand_story",
                "product_name": "赣南脐橙",
                "keywords": ["新鲜", "产地直发", "绿色有机"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert len(data["content"]) > 0

    @pytest.mark.anyio
    async def test_generate_product_selling_points(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/ai/copywriting",
            json={
                "type": "selling_points",
                "product_name": "土蜂蜜",
                "keywords": ["天然", "野生"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert "items" in data["content"]


class TestFieldExtraction:
    """W17-002: 产品资料字段提取"""

    @pytest.mark.anyio
    async def test_extract_product_info(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/ai/extract",
            json={
                "text": "本产品为赣南脐橙，产地江西赣州，净重5kg，保质期15天，储存方式：冷藏",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "fields" in data
        assert "product_name" in data["fields"]
        assert "origin" in data["fields"]


class TestPageSuggestion:
    """W17-003: 页面结构建议"""

    @pytest.mark.anyio
    async def test_suggest_page_structure(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/ai/page-suggest",
            json={
                "product_name": "有机大米",
                "category": "粮食",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert len(data["modules"]) > 0


class TestCampaignGeneration:
    """W17-004: 活动方案生成"""

    @pytest.mark.anyio
    async def test_generate_campaign(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/ai/campaign",
            json={
                "product_name": "赣南脐橙",
                "goal": "promotion",
                "target_audience": "年轻消费者",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "description" in data
        assert "suggested_benefits" in data
