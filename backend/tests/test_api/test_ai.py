"""W17: AI 资料识别与文案生成测试"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

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


@pytest.fixture(autouse=True)
def _mock_llm():
    with patch("app.services.ai._check_daily_limit", new_callable=AsyncMock), \
         patch("app.services.ai._increment_daily_count", new_callable=AsyncMock), \
         patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save, \
         patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_save.return_value = type("R", (), {"id": uuid.uuid4()})()
        yield mock_llm


class TestCopywriting:
    """W17-001: AI 文案生成"""

    @pytest.mark.anyio
    async def test_generate_brand_story(self, client: AsyncClient, setup_tenant, _mock_llm):
        _mock_llm.return_value = {"content": "赣南脐橙，大自然的馈赠，品质之选"}
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

    @pytest.mark.anyio
    async def test_generate_product_selling_points(
        self,
        client: AsyncClient,
        setup_tenant,
        _mock_llm,
    ):
        _mock_llm.return_value = {"content": {"items": ["天然纯正", "野生采集"]}}
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


class TestFieldExtraction:
    """W17-002: 产品资料字段提取"""

    @pytest.mark.anyio
    async def test_extract_product_info(self, client: AsyncClient, setup_tenant, _mock_llm):
        _mock_llm.return_value = {
            "product_name": "赣南脐橙",
            "origin": "江西赣州",
            "weight": "5kg",
            "shelf_life": "15天",
        }
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
    async def test_suggest_page_structure(self, client: AsyncClient, setup_tenant, _mock_llm):
        _mock_llm.return_value = {"modules": ["hero_banner", "origin_map", "nutrition_facts"]}
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
        assert "suggestion" in data
        modules = data["suggestion"]["modules"]
        assert len(modules) > 0


class TestCampaignGeneration:
    """W17-004: 活动方案生成"""

    @pytest.mark.anyio
    async def test_generate_campaign(self, client: AsyncClient, setup_tenant, _mock_llm):
        _mock_llm.return_value = {
            "name": "脐橙尝鲜季",
            "description": "限时优惠",
            "suggested_benefits": [{"type": "coupon", "value": 10}],
        }
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
        assert "campaign" in data
        assert data["campaign"]["name"] == "脐橙尝鲜季"
