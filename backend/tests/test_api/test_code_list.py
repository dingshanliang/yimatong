"""A4-006: 码查询与列表 API 验收测试"""

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
async def setup_batch_with_codes(client: AsyncClient):
    """创建租户+品牌+产品+SKU+码批次"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "查询测试",
            "admin_email": "query@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "查询品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "查询产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "Q-SKU", "name": "Q SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "Q-PB-001",
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


class TestCodeBatchList:
    @pytest.mark.anyio
    async def test_list_batches_with_pagination(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, _ = setup_batch_with_codes
        resp = await client.get(
            "/api/v1/code-batches?page=1&page_size=10",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert data["total"] >= 1

    @pytest.mark.anyio
    async def test_list_batches_filter_by_product(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, batch_id = setup_batch_with_codes
        # 先获取批次信息拿到 product_id
        detail = await client.get(
            f"/api/v1/code-batches/{batch_id}",
            headers=headers,
        )
        product_id = detail.json()["product_id"]

        resp = await client.get(
            f"/api/v1/code-batches?product_id={product_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestCodeBatchDetail:
    @pytest.mark.anyio
    async def test_get_batch_detail(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, batch_id = setup_batch_with_codes
        resp = await client.get(
            f"/api/v1/code-batches/{batch_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == batch_id
        assert data["batch_code"] == "Q-PB-001"
        assert data["quantity"] == 5
        assert data["production_batch_code"] == "Q-PB-001"
        assert data["generation_mode"] == "item_level"
        # 包含各状态码数量统计
        assert "stats" in data
        assert "created" in data["stats"]

    @pytest.mark.anyio
    async def test_get_batch_detail_not_found(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, _ = setup_batch_with_codes
        fake_id = "00000000-0000-0000-0000-000000000999"
        resp = await client.get(
            f"/api/v1/code-batches/{fake_id}",
            headers=headers,
        )
        assert resp.status_code == 404


class TestCodeItemDetail:
    @pytest.mark.anyio
    async def test_get_code_item_detail(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, batch_id = setup_batch_with_codes
        # 先列出码项
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
            headers=headers,
        )
        item_id = items_resp.json()["items"][0]["id"]

        resp = await client.get(
            f"/api/v1/code-items/{item_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == item_id
        assert data["status"] == "created"
        assert "public_id" in data

    @pytest.mark.anyio
    async def test_get_code_item_not_found(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, _ = setup_batch_with_codes
        fake_id = "00000000-0000-0000-0000-000000000999"
        resp = await client.get(
            f"/api/v1/code-items/{fake_id}",
            headers=headers,
        )
        assert resp.status_code == 404


class TestCodeBatchStats:
    @pytest.mark.anyio
    async def test_batch_stats_after_activate(self, client: AsyncClient, setup_batch_with_codes):
        _, headers, batch_id = setup_batch_with_codes

        # 激活前：全部 created
        detail = await client.get(
            f"/api/v1/code-batches/{batch_id}",
            headers=headers,
        )
        stats = detail.json()["stats"]
        assert stats["created"] == 5

        # 激活批次
        await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )

        # 激活后：全部 activated
        detail = await client.get(
            f"/api/v1/code-batches/{batch_id}",
            headers=headers,
        )
        stats = detail.json()["stats"]
        assert stats["activated"] == 5
        assert stats.get("created", 0) == 0
