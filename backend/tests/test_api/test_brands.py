"""A3-001: Brand 数据模型与 CRUD API 验收测试"""

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
            "name": "品牌测试",
            "admin_email": "brand@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


class TestBrandCRUD:
    @pytest.mark.anyio
    async def test_create_brand(self, client: AsyncClient, tenant_with_auth):
        tid, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/brands",
            json={"name": "金龙鱼", "description": "粮油品牌"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "金龙鱼"
        assert data["tenant_id"] == tid
        assert data["status"] == "active"

    @pytest.mark.anyio
    async def test_list_brands_paginated(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        for i in range(3):
            await client.post("/api/v1/brands", json={"name": f"品牌{i}"}, headers=headers)

        resp = await client.get("/api/v1/brands?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["items"]) <= 2

    @pytest.mark.anyio
    async def test_list_brands_search_by_name(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        await client.post("/api/v1/brands", json={"name": "金龙鱼"}, headers=headers)
        await client.post("/api/v1/brands", json={"name": "福临门"}, headers=headers)

        resp = await client.get("/api/v1/brands?name=金", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all("金" in item["name"] for item in data["items"])

    @pytest.mark.anyio
    async def test_update_brand(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.post("/api/v1/brands", json={"name": "原品牌"}, headers=headers)
        brand_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/brands/{brand_id}",
            json={"name": "新品牌", "description": "更新后"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新品牌"
        assert resp.json()["description"] == "更新后"

    @pytest.mark.anyio
    async def test_brand_name_unique_per_tenant(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        await client.post("/api/v1/brands", json={"name": "唯一品牌"}, headers=headers)
        resp = await client.post("/api/v1/brands", json={"name": "唯一品牌"}, headers=headers)
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_delete_brand_success(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.post("/api/v1/brands", json={"name": "待删品牌"}, headers=headers)
        brand_id = resp.json()["id"]

        resp = await client.delete(f"/api/v1/brands/{brand_id}", headers=headers)
        assert resp.status_code == 204

        get_resp = await client.get(f"/api/v1/brands/{brand_id}", headers=headers)
        assert get_resp.status_code == 404

    @pytest.mark.anyio
    async def test_delete_brand_with_products_blocked(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        brand_resp = await client.post("/api/v1/brands", json={"name": "有产品品牌"}, headers=headers)
        brand_id = brand_resp.json()["id"]
        await client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "关联产品"},
            headers=headers,
        )

        resp = await client.delete(f"/api/v1/brands/{brand_id}", headers=headers)
        assert resp.status_code == 409
        assert "product" in resp.json()["detail"].lower() or "关联" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_delete_brand_not_found(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.delete(f"/api/v1/brands/{fake_id}", headers=headers)
        assert resp.status_code == 404
