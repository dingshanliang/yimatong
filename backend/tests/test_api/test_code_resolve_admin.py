"""A4-007: Resolver Stub 管理端接口验收测试"""

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
async def setup_codes(client: AsyncClient):
    """创建租户+产品+SKU+码批次，返回 headers 和第一个 public_id"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "解析测试",
            "admin_email": "resolve@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "解析品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "解析产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "R-SKU", "name": "R SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "R-001",
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
            "quantity": 3,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]

    # 获取第一个码项的 public_id
    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
        headers=headers,
    )
    first_item = items.json()["items"][0]
    return tid, headers, first_item["id"], first_item["public_id"], batch_id


class TestCodeResolveAdmin:
    @pytest.mark.anyio
    async def test_resolve_by_public_id(self, client: AsyncClient, setup_codes):
        _, headers, item_id, public_id, _ = setup_codes
        resp = await client.get(
            f"/api/v1/code-items/public/{public_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["public_id"] == public_id
        assert data["status"] == "created"
        assert "product_id" in data
        assert "sku_id" in data

    @pytest.mark.anyio
    async def test_resolve_not_found(self, client: AsyncClient, setup_codes):
        _, headers, _, _, _ = setup_codes
        resp = await client.get(
            "/api/v1/code-items/public/NONEXISTENT",
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_resolve_revoked_returns_gone(self, client: AsyncClient, setup_codes):
        _, headers, item_id, _, _ = setup_codes
        # 先激活再作废
        await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            headers=headers,
        )
        # 此时码已经 revoked

        # 需要通过 public_id 获取，先从详情拿到 public_id
        detail = await client.get(
            f"/api/v1/code-items/{item_id}",
            headers=headers,
        )
        public_id = detail.json()["public_id"]

        resp = await client.get(
            f"/api/v1/code-items/public/{public_id}",
            headers=headers,
        )
        assert resp.status_code == 410
        assert "revoked" in resp.json()["detail"].lower() or "gone" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_resolve_requires_auth(self, client: AsyncClient, setup_codes):
        _, _, _, public_id, _ = setup_codes
        resp = await client.get(
            f"/api/v1/code-items/public/{public_id}",
        )
        assert resp.status_code == 401
