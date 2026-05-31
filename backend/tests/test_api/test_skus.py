"""A3-003: SKU 数据模型与 CRUD API 验收测试"""

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
            "name": "SKU测试",
            "admin_email": "sku@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def product_id(client: AsyncClient, tenant_with_auth):
    _, headers = tenant_with_auth
    brand_resp = await client.post("/api/v1/brands", json={"name": "SKU品牌"}, headers=headers)
    brand_id = brand_resp.json()["id"]
    prod_resp = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "SKU产品", "category": "食用油"},
        headers=headers,
    )
    return prod_resp.json()["id"]


class TestSKUCRUD:
    @pytest.mark.anyio
    async def test_create_sku(self, client: AsyncClient, tenant_with_auth, product_id):
        tid, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/skus",
            json={
                "product_id": product_id,
                "code": "SKU-001",
                "name": "金龙鱼调和油500ml",
                "specifications": {"capacity": "500ml", "weight": "0.5kg"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "金龙鱼调和油500ml"
        assert data["tenant_id"] == tid
        assert data["product_id"] == product_id
        assert data["code"] == "SKU-001"
        assert data["specifications"]["capacity"] == "500ml"

    @pytest.mark.anyio
    async def test_list_skus_paginated(self, client: AsyncClient, tenant_with_auth, product_id):
        _, headers = tenant_with_auth
        for i in range(3):
            await client.post(
                "/api/v1/skus",
                json={
                    "product_id": product_id,
                    "code": f"SKU-P{i:03d}",
                    "name": f"规格{i}",
                },
                headers=headers,
            )

        resp = await client.get("/api/v1/skus?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["items"]) <= 2

    @pytest.mark.anyio
    async def test_list_skus_filter_by_product(self, client: AsyncClient, tenant_with_auth, product_id):
        _, headers = tenant_with_auth
        # Create second product
        brand_resp = await client.post("/api/v1/brands", json={"name": "品牌B"}, headers=headers)
        brand2_id = brand_resp.json()["id"]
        prod2_resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand2_id, "name": "产品B"},
            headers=headers,
        )
        product2_id = prod2_resp.json()["id"]

        await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "SKU-A1", "name": "SKU-A"},
            headers=headers,
        )
        await client.post(
            "/api/v1/skus",
            json={"product_id": product2_id, "code": "SKU-B1", "name": "SKU-B"},
            headers=headers,
        )

        resp = await client.get(f"/api/v1/skus?product_id={product_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["product_id"] == product_id for item in data["items"])

    @pytest.mark.anyio
    async def test_sku_code_unique_per_product(self, client: AsyncClient, tenant_with_auth, product_id):
        _, headers = tenant_with_auth
        await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "DUP-001", "name": "SKU1"},
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/skus",
            json={"product_id": product_id, "code": "DUP-001", "name": "SKU2"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_update_sku(self, client: AsyncClient, tenant_with_auth, product_id):
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/skus",
            json={
                "product_id": product_id,
                "code": "SKU-UPD",
                "name": "原名",
                "specifications": {"size": "500ml"},
                "package_type": "瓶装",
                "barcode": "6901234567890",
                "image_url": "https://example.com/sku.png",
            },
            headers=headers,
        )
        sku_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/skus/{sku_id}",
            json={"name": "新名", "specifications": {"size": "1L"}},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"
        assert resp.json()["specifications"]["size"] == "1L"

        clear_resp = await client.patch(
            f"/api/v1/skus/{sku_id}",
            json={
                "specifications": None,
                "package_type": None,
                "barcode": None,
                "image_url": None,
            },
            headers=headers,
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["specifications"] is None
        assert clear_resp.json()["package_type"] is None
        assert clear_resp.json()["barcode"] is None
        assert clear_resp.json()["image_url"] is None
