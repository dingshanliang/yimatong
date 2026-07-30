"""A7-006: 码状态统计 API 测试"""

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
async def auth_with_codes(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "码统计测试",
            "admin_email": "codestats@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "统计品牌"}, headers=headers)
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "统计产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": prod.json()["id"], "code": "CS-SKU", "name": "CS SKU"},
        headers=headers,
    )
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "CS-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
        },
        headers=headers,
    )
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": production_batch.json()["id"],
            "quantity": 5,
        },
        headers=headers,
    )
    # 激活码
    await client.post(
        f"/api/v1/code-batches/{batch.json()['id']}/activate",
        headers=headers,
    )
    return tid, headers, batch.json()["id"]


class TestCodeStatsAPI:
    @pytest.mark.anyio
    async def test_code_stats(self, client: AsyncClient, auth_with_codes):
        _, headers, _ = auth_with_codes
        resp = await client.get(
            "/api/v1/analytics/code-stats",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert "activated" in data["by_status"]

    @pytest.mark.anyio
    async def test_code_stats_by_batch(self, client: AsyncClient, auth_with_codes):
        _, headers, batch_id = auth_with_codes
        resp = await client.get(
            f"/api/v1/analytics/code-stats?code_batch_id={batch_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 5
