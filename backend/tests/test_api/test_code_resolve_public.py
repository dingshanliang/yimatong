"""A6-001: 码解析公开路由验收测试"""

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
async def setup_activated_code(client: AsyncClient):
    """创建租户+产品+SKU+码批次并激活"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "解析公开测试",
            "admin_email": "public@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "公开品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "公开产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "P-SKU", "name": "P SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "P-001",
            "quantity": 3,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]

    # 获取第一个码
    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
        headers=headers,
    )
    first_item = items.json()["items"][0]
    public_id = first_item["public_id"]
    item_id = first_item["id"]

    # 激活
    await client.post(
        f"/api/v1/code-batches/{batch_id}/activate",
        headers=headers,
    )

    return tid, headers, batch_id, item_id, public_id


class TestPublicResolve:
    @pytest.mark.anyio
    async def test_resolve_activated_code(self, client: AsyncClient, setup_activated_code):
        _, _, _, _, public_id = setup_activated_code
        resp = await client.get(f"/c/{public_id}")
        assert resp.status_code == 200
        # 应返回 HTML 内容
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_resolve_nonexistent_code(self, client: AsyncClient, setup_activated_code):
        resp = await client.get("/c/NONEXISTENT123")
        assert resp.status_code == 404
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_resolve_revoked_code(self, client: AsyncClient, setup_activated_code):
        _, headers, _, item_id, public_id = setup_activated_code
        # 作废码
        await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            headers=headers,
        )
        resp = await client.get(f"/c/{public_id}")
        assert resp.status_code == 410
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_resolve_created_code(self, client: AsyncClient, setup_activated_code):
        """未激活的码返回提示页"""
        # 创建新码但不激活
        _, headers, batch_id, _, _ = setup_activated_code
        # 获取第二个码（仍为 created 状态）
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page_size=10",
            headers=headers,
        )
        for item in items.json()["items"]:
            if item["status"] == "created":
                resp = await client.get(f"/c/{item['public_id']}")
                # 返回提示页
                assert resp.status_code == 200
                assert "text/html" in resp.headers.get("content-type", "")
                break

    @pytest.mark.anyio
    async def test_no_auth_required(self, client: AsyncClient, setup_activated_code):
        """公开路由不需要认证"""
        _, _, _, _, public_id = setup_activated_code
        resp = await client.get(f"/c/{public_id}")
        # 不带任何 auth header 也能访问
        assert resp.status_code != 401
