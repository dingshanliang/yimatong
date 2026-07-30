"""A4-004: 码状态转换 API 测试"""

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
async def batch_with_codes(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "状态测试",
            "admin_email": "state@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "S品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "S产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "S-SKU", "name": "S SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "SB-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
        },
        headers=headers,
    )

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch.json()["id"],
            "quantity": 5,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]
    return tid, headers, batch_id


class TestCodeStateTransitions:
    @pytest.mark.anyio
    async def test_activate_batch(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["activated"] == 5

        list_resp = await client.get("/api/v1/code-batches", headers=headers)
        assert list_resp.status_code == 200
        batch = next(item for item in list_resp.json()["items"] if item["id"] == batch_id)
        assert batch["status"] == "activated"

    @pytest.mark.anyio
    async def test_activate_batch_twice_returns_conflict(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        first_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )
        assert first_resp.status_code == 200

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )
        assert resp.status_code == 409
        assert "already activated" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_revoke_single_code(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        # Activate first
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        # Get a code item
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
            headers=headers,
        )
        item_id = items_resp.json()["items"][0]["id"]

        resp = await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    @pytest.mark.anyio
    async def test_invalid_transition_blocked(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        # Try to bind without activating (created -> bound is invalid)
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
            headers=headers,
        )
        item_id = items_resp.json()["items"][0]["id"]

        resp = await client.post(
            f"/api/v1/code-items/{item_id}/bind",
            headers=headers,
        )
        assert resp.status_code == 409
