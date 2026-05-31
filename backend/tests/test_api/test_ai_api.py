"""AI API 端点集成测试"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session():
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
def auth_token():
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    return create_access_token(tenant_id, account_id, "admin")


@pytest.fixture
def auth_client(client: AsyncClient, auth_token: str):
    client.headers["Authorization"] = f"Bearer {auth_token}"
    return client


# 模拟 LLM 返回值，避免真实 API 调用
MOCK_EXTRACT_RESULT = {
    "product_name": "脐橙",
    "category": "水果",
    "origin": "江西赣州",
}
MOCK_COPYWRITING_RESULT = {"content": "赣南脐橙，大自然的馈赠"}
MOCK_PAGE_SUGGEST_RESULT = {"modules": ["hero_banner", "origin_map", "reviews"]}
MOCK_PAGE_COPY_RESULT = {
    "copywriting": "优质脐橙",
    "recommended_template": {"template_type": "traceability"},
    "page_suggestion": {"modules": ["hero_banner"]},
}
MOCK_CAMPAIGN_RESULT = {
    "name": "脐橙尝鲜季",
    "description": "限时优惠活动",
    "suggested_benefits": [{"type": "coupon", "value": 10}],
}
MOCK_IMAGE_RESULT = {"product_name": "蜂蜜", "category": "蜂蜜"}


@pytest.fixture(autouse=True)
def _mock_llm():
    with patch("app.services.ai._check_daily_limit", new_callable=AsyncMock), \
         patch("app.services.ai._increment_daily_count", new_callable=AsyncMock), \
         patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save, \
         patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_save.return_value = type("R", (), {"id": uuid.uuid4()})()
        yield mock_llm


@pytest.mark.asyncio
async def test_extract_text(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_EXTRACT_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/extract",
        json={"text": "赣南脐橙，产地江西赣州"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert data["fields"]["product_name"] == "脐橙"


@pytest.mark.asyncio
async def test_recognize_image(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_IMAGE_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/recognize-image",
        json={"image_url": "https://example.com/product.jpg", "filename": "product.jpg"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert "product_name" in data["fields"]
    assert "category" in data["fields"]


@pytest.mark.asyncio
async def test_recognize_image_rejects_no_url(auth_client):
    resp = await auth_client.post(
        "/api/v1/ai/recognize-image",
        json={"filename": "doc.pdf"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_page_copy(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_PAGE_COPY_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/page-copy",
        json={
            "product_name": "赣南脐橙",
            "category": "水果",
            "keywords": ["新鲜", "有机"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert "generation_id" in data


@pytest.mark.asyncio
async def test_copywriting(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_COPYWRITING_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/copywriting",
        json={"type": "brand_story", "product_name": "蜂蜜", "keywords": ["天然"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


@pytest.mark.asyncio
async def test_page_suggest(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_PAGE_SUGGEST_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/page-suggest",
        json={"product_name": "脐橙", "category": "水果"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "suggestion" in data
    assert "modules" in data["suggestion"]


@pytest.mark.asyncio
async def test_campaign(auth_client, _mock_llm):
    _mock_llm.return_value = MOCK_CAMPAIGN_RESULT
    resp = await auth_client.post(
        "/api/v1/ai/campaign",
        json={
            "product_name": "蜂蜜",
            "goal": "promotion",
            "target_audience": "年轻消费者",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "campaign" in data
    assert data["campaign"]["name"] == "脐橙尝鲜季"
