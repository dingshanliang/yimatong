"""AI API 端点集成测试"""

import io
import uuid

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


@pytest.mark.asyncio
async def test_extract_text(auth_client):
    resp = await auth_client.post(
        "/api/v1/ai/extract",
        json={"text": "赣南脐橙，产地江西赣州"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert data["fields"]["product_name"] == "脐橙"


@pytest.mark.asyncio
async def test_recognize_image(auth_client):
    fake_image = io.BytesIO(b"fake-jpg-content")
    resp = await auth_client.post(
        "/api/v1/ai/recognize-image",
        files={"file": ("orange.jpg", fake_image, "image/jpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert "product_name" in data["fields"]
    assert "category" in data["fields"]


@pytest.mark.asyncio
async def test_recognize_image_rejects_pdf(auth_client):
    fake_pdf = io.BytesIO(b"fake-pdf-content")
    resp = await auth_client.post(
        "/api/v1/ai/recognize-image",
        files={"file": ("doc.pdf", fake_pdf, "application/pdf")},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_page_copy(auth_client):
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
    assert "copywriting" in data
    assert "recommended_template" in data
    assert "page_suggestion" in data


@pytest.mark.asyncio
async def test_copywriting(auth_client):
    resp = await auth_client.post(
        "/api/v1/ai/copywriting",
        json={"type": "brand_story", "product_name": "蜂蜜", "keywords": ["天然"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


@pytest.mark.asyncio
async def test_page_suggest(auth_client):
    resp = await auth_client.post(
        "/api/v1/ai/page-suggest",
        json={"product_name": "脐橙", "category": "水果"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "modules" in data


@pytest.mark.asyncio
async def test_campaign(auth_client):
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
    assert "name" in data
    assert "description" in data
