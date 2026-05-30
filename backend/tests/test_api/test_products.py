"""A3-002: Product 数据模型与 CRUD API 验收测试"""

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
            "name": "产品测试",
            "admin_email": "product@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def brand_id(client: AsyncClient, tenant_with_auth):
    _, headers = tenant_with_auth
    resp = await client.post("/api/v1/brands", json={"name": "测试品牌"}, headers=headers)
    return resp.json()["id"]


class TestProductCRUD:
    @pytest.mark.anyio
    async def test_create_product(self, client: AsyncClient, tenant_with_auth, brand_id):
        tid, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "金龙鱼调和油", "category": "食用油"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "金龙鱼调和油"
        assert data["tenant_id"] == tid
        assert data["brand_id"] == brand_id
        assert data["category"] == "食用油"

    @pytest.mark.anyio
    async def test_list_products_paginated(self, client: AsyncClient, tenant_with_auth, brand_id):
        _, headers = tenant_with_auth
        for i in range(3):
            await client.post(
                "/api/v1/products",
                json={"brand_id": brand_id, "name": f"产品{i}"},
                headers=headers,
            )

        resp = await client.get("/api/v1/products?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["items"]) <= 2

    @pytest.mark.anyio
    async def test_list_products_filter_by_brand(self, client: AsyncClient, tenant_with_auth, brand_id):
        _, headers = tenant_with_auth
        # Create second brand
        resp2 = await client.post("/api/v1/brands", json={"name": "品牌2"}, headers=headers)
        brand2_id = resp2.json()["id"]

        await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "A产品"},
            headers=headers,
        )
        await client.post(
            "/api/v1/products",
            json={"brand_id": brand2_id, "name": "B产品"},
            headers=headers,
        )

        resp = await client.get(f"/api/v1/products?brand_id={brand_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["brand_id"] == brand_id for item in data["items"])

    @pytest.mark.anyio
    async def test_update_product(self, client: AsyncClient, tenant_with_auth, brand_id):
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "原产品"},
            headers=headers,
        )
        pid = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/products/{pid}",
            json={"name": "新产品", "category": "新分类"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新产品"
        assert resp.json()["category"] == "新分类"

    @pytest.mark.anyio
    async def test_list_products_filter_by_category(self, client: AsyncClient, tenant_with_auth, brand_id):
        _, headers = tenant_with_auth
        await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "产品A", "category": "食用油"},
            headers=headers,
        )
        await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "产品B", "category": "大米"},
            headers=headers,
        )

        resp = await client.get("/api/v1/products?category=食用油", headers=headers)
        data = resp.json()
        assert all(item["category"] == "食用油" for item in data["items"])
