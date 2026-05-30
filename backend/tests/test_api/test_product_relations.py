"""A3-007: 产品资料关联查询与验证 验收测试"""

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
async def tenant_with_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "关联测试",
            "admin_email": "rel@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def brand_with_products(client: AsyncClient, tenant_with_auth):
    _, headers = tenant_with_auth
    brand_resp = await client.post("/api/v1/brands", json={"name": "品牌A"}, headers=headers)
    brand_id = brand_resp.json()["id"]

    p1 = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "产品1"},
        headers=headers,
    )
    p2 = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "产品2"},
        headers=headers,
    )
    return brand_id, [p1.json()["id"], p2.json()["id"]]


class TestProductRelations:
    @pytest.mark.anyio
    async def test_brand_products(self, client: AsyncClient, tenant_with_auth, brand_with_products):
        brand_id, product_ids = brand_with_products
        _, headers = tenant_with_auth

        resp = await client.get(f"/api/v1/brands/{brand_id}/products", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert all(p["brand_id"] == brand_id for p in data["items"])

    @pytest.mark.anyio
    async def test_product_skus(self, client: AsyncClient, tenant_with_auth, brand_with_products):
        _, product_ids = brand_with_products
        _, headers = tenant_with_auth
        product_id = product_ids[0]

        await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "SKU-R1", "name": "SKU1"},
            headers=headers,
        )
        await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "SKU-R2", "name": "SKU2"},
            headers=headers,
        )

        resp = await client.get(f"/api/v1/products/{product_id}/skus", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert all(s["product_id"] == product_id for s in data["items"])

    @pytest.mark.anyio
    async def test_product_batches(self, client: AsyncClient, tenant_with_auth, brand_with_products):
        _, product_ids = brand_with_products
        _, headers = tenant_with_auth
        product_id = product_ids[0]

        # Create SKU first
        sku_resp = await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "SKU-B1", "name": "BatchSKU"},
            headers=headers,
        )
        sku_id = sku_resp.json()["id"]

        await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "BATCH-R1",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )

        resp = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1
        assert all(b["product_id"] == product_id for b in data["items"])

    @pytest.mark.anyio
    async def test_relation_tenant_isolation(self, client: AsyncClient, tenant_with_auth, brand_with_products):
        brand_id, _ = brand_with_products
        _, headers = tenant_with_auth

        # Create second tenant
        resp2 = await client.post(
            "/api/v1/tenants",
            json={
                "name": "隔离租户",
                "admin_email": "iso@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
        )
        tid2 = resp2.json()["id"]
        token2 = create_access_token(tid2, "00000000-0000-0000-0000-000000000002", "admin")
        headers2 = {"Authorization": f"Bearer {token2}"}

        # Tenant 2 should not see tenant 1's brand products
        resp = await client.get(f"/api/v1/brands/{brand_id}/products", headers=headers2)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert len(resp.json()["items"]) == 0

    @pytest.mark.anyio
    async def test_delete_brand_with_products_blocked(self, client: AsyncClient, tenant_with_auth, brand_with_products):
        brand_id, _ = brand_with_products
        _, headers = tenant_with_auth

        resp = await client.delete(f"/api/v1/brands/{brand_id}", headers=headers)
        assert resp.status_code == 409
        assert "product" in resp.json()["detail"].lower()
