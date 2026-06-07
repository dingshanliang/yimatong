"""A4-003: 码批次创建 API 验收测试"""

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
async def tenant_with_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "码测试",
            "admin_email": "code@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sku_with_auth(client: AsyncClient, tenant_with_auth):
    tid, headers = tenant_with_auth
    brand_resp = await client.post("/api/v1/brands", json={"name": "码品牌"}, headers=headers)
    brand_id = brand_resp.json()["id"]
    prod_resp = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "码产品"},
        headers=headers,
    )
    product_id = prod_resp.json()["id"]
    sku_resp = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "CODE-SKU", "name": "码SKU"},
        headers=headers,
    )
    sku_id = sku_resp.json()["id"]
    batch_resp = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "PB-CODE-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
            "origin": "黑龙江省哈尔滨市五常市",
        },
        headers=headers,
    )
    return product_id, sku_id, batch_resp.json()["id"]


class TestCodeBatchCreate:
    @pytest.mark.anyio
    async def test_create_code_batch(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        tid, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 10,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["batch_code"] == "PB-CODE-001"
        assert data["quantity"] == 10
        assert data["generated_count"] == 10
        assert data["status"] == "completed"
        assert data["generation_mode"] == "item_level"
        assert data["production_batch_id"] == production_batch_id

    @pytest.mark.anyio
    async def test_batch_creates_code_items(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        # Check code items were created
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        assert items_resp.status_code == 200
        assert items_resp.json()["total"] == 5

    @pytest.mark.anyio
    async def test_code_items_have_unique_public_ids(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 20,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page_size=20",
            headers=headers,
        )
        items = items_resp.json()["items"]
        public_ids = [item["public_id"] for item in items]
        assert len(public_ids) == len(set(public_ids))

    @pytest.mark.anyio
    async def test_batch_level_code_forces_single_item(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 50,
                "code_type": "paired",
                "generation_mode": "batch_level",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["quantity"] == 1
        assert data["generated_count"] == 1
        assert data["code_type"] == "single"
        assert data["generation_mode"] == "batch_level"

    @pytest.mark.anyio
    async def test_rejects_production_batch_from_another_sku(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        _, headers = tenant_with_auth
        product_id, sku_id, _ = sku_with_auth
        other_sku_resp = await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "OTHER-CODE-SKU", "name": "其他 SKU"},
            headers=headers,
        )
        other_batch_resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": other_sku_resp.json()["id"],
                "batch_code": "PB-CODE-OTHER",
                "production_date": "2026-05-31",
                "expiry_date": "2027-05-31",
            },
            headers=headers,
        )

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": other_batch_resp.json()["id"],
                "quantity": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Production batch does not belong" in resp.json()["detail"]
