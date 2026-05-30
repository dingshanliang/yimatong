"""W19: Webhook / Open API 测试"""

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
            "name": "Webhook测试租户",
            "admin_email": "webhook@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestWebhookManagement:
    """W19-001: Webhook 端点管理"""

    @pytest.mark.anyio
    async def test_create_webhook(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/webhook",
                "events": ["scan", "claim"],
                "secret": "wh_secret_123",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/webhook"
        assert "scan" in data["events"]

    @pytest.mark.anyio
    async def test_list_webhooks(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/wh2",
                "events": ["risk_alert"],
                "secret": "secret2",
            },
            headers=headers,
        )

        resp = await client.get("/api/v1/webhooks/endpoints", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestApiKey:
    """W19-002: API Key 认证"""

    @pytest.mark.anyio
    async def test_create_api_key(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json={"name": "外部系统密钥", "permissions": ["read:scans", "read:products"]},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "外部系统密钥"
        assert "key" in data
        assert len(data["key"]) > 0

    @pytest.mark.anyio
    async def test_list_api_keys(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/webhooks/api-keys",
            json={"name": "列表测试密钥", "permissions": ["read:scans"]},
            headers=headers,
        )

        resp = await client.get("/api/v1/webhooks/api-keys", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.anyio
    async def test_revoke_api_key(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json={"name": "待吊销密钥", "permissions": ["read:scans"]},
            headers=headers,
        )
        key_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/webhooks/api-keys/{key_id}",
            headers=headers,
        )
        assert resp.status_code == 200


class TestEventDelivery:
    """W19-003: 事件推送"""

    @pytest.mark.anyio
    async def test_list_deliveries(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.get(
            "/api/v1/webhooks/deliveries",
            headers=headers,
        )
        assert resp.status_code == 200
