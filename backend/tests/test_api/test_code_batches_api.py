"""码批次和码项 API 端点测试

使用 httpx AsyncClient + ASGITransport 测试 API 端点：
- POST /api/v1/code-batches 创建码批次
- GET /api/v1/code-batches 列表
- POST /api/v1/code-batches/{id}/activate 激活
- POST /api/v1/code-batches/{id}/export 导出 CSV
- POST /api/v1/code-batches/{id}/freeze 冻结
- POST /api/v1/code-batches/{id}/void 作废
- GET /api/v1/code-items 码项列表
"""

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
async def auth_setup(client: AsyncClient):
    """创建租户、认证 token、品牌、产品、SKU、生产批次"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "码API测试",
            "admin_email": "codeapi@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code in (200, 201)
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "码API品牌"}, headers=headers)
    assert brand.status_code == 201
    brand_id = brand.json()["id"]

    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "码API产品"},
        headers=headers,
    )
    assert prod.status_code == 201
    product_id = prod.json()["id"]

    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "API-SKU", "name": "码API SKU"},
        headers=headers,
    )
    assert sku.status_code == 201
    sku_id = sku.json()["id"]

    batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "PB-API-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
            "origin": "黑龙江",
        },
        headers=headers,
    )
    assert batch.status_code == 201
    production_batch_id = batch.json()["id"]

    return tid, headers, product_id, sku_id, production_batch_id


class TestCreateCodeBatchAPI:
    """POST /api/v1/code-batches 创建码批次"""

    @pytest.mark.anyio
    async def test_create_batch(self, client: AsyncClient, auth_setup):
        tid, headers, product_id, sku_id, production_batch_id = auth_setup

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
        assert data["batch_code"] == "PB-API-001"
        assert data["quantity"] == 10
        assert data["status"] == "completed"
        assert data["generation_mode"] == "item_level"
        assert "id" in data

    @pytest.mark.anyio
    async def test_create_batch_missing_product(self, client: AsyncClient, auth_setup):
        tid, headers, _, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": "00000000-0000-0000-0000-000000000999",
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
            headers=headers,
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_create_batch_requires_auth(self, client: AsyncClient, auth_setup):
        _, _, product_id, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
        )
        assert resp.status_code == 401


class TestListCodeBatchesAPI:
    """GET /api/v1/code-batches 列表"""

    @pytest.mark.anyio
    async def test_list_batches(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        # 先创建两个批次
        await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
            headers=headers,
        )
        await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 3,
            },
            headers=headers,
        )

        resp = await client.get("/api/v1/code-batches", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    @pytest.mark.anyio
    async def test_list_batches_with_status_filter(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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

        # 激活一个
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        # 过滤 activated
        filtered = await client.get(
            "/api/v1/code-batches?status=activated", headers=headers
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1

        # 过滤 completed 应该为 0
        completed = await client.get(
            "/api/v1/code-batches?status=completed", headers=headers
        )
        assert completed.status_code == 200
        assert completed.json()["total"] == 0


class TestActivateCodeBatchAPI:
    """POST /api/v1/code-batches/{id}/activate 激活"""

    @pytest.mark.anyio
    async def test_activate_batch(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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

        activate_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate", headers=headers
        )
        assert activate_resp.status_code == 200
        assert activate_resp.json()["activated"] == 5

    @pytest.mark.anyio
    async def test_activate_nonexistent_batch(self, client: AsyncClient, auth_setup):
        _, headers, *_ = auth_setup

        resp = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/activate",
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_activate_already_activated(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 3,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
        # 再次激活应失败
        dup_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate", headers=headers
        )
        assert dup_resp.status_code == 409


class TestExportCodeBatchAPI:
    """POST /api/v1/code-batches/{id}/export 导出 CSV"""

    @pytest.mark.anyio
    async def test_export_csv(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 3,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        export_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/export", headers=headers
        )
        assert export_resp.status_code == 200
        assert "text/csv" in export_resp.headers.get("content-type", "")
        assert "codes-" in export_resp.headers.get("content-disposition", "")

        csv_text = export_resp.text
        # CSV 头 + 3 行数据
        lines = [l for l in csv_text.strip().split("\n") if l.strip()]
        assert len(lines) == 4  # 1 header + 3 data rows
        assert "public_id" in lines[0]

    @pytest.mark.anyio
    async def test_export_nonexistent_batch(self, client: AsyncClient, auth_setup):
        _, headers, *_ = auth_setup

        resp = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/export",
            headers=headers,
        )
        # 导出不存在的批次应返回 404
        assert resp.status_code == 404


class TestFreezeCodeBatchAPI:
    """POST /api/v1/code-batches/{id}/freeze 冻结"""

    @pytest.mark.anyio
    async def test_freeze_activated_batch(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        freeze_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/freeze", headers=headers
        )
        assert freeze_resp.status_code == 200
        assert freeze_resp.json()["frozen"] == 5

    @pytest.mark.anyio
    async def test_freeze_nonexistent_batch(self, client: AsyncClient, auth_setup):
        _, headers, *_ = auth_setup

        resp = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/freeze",
            headers=headers,
        )
        # freeze 没找到码项时返回 frozen=0
        assert resp.status_code == 200
        assert resp.json()["frozen"] == 0


class TestVoidCodeBatchAPI:
    """POST /api/v1/code-batches/{id}/void 作废"""

    @pytest.mark.anyio
    async def test_void_batch(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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

        void_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=test-void&confirm=void", headers=headers
        )
        assert void_resp.status_code == 200
        assert void_resp.json()["voided"] == 5

    @pytest.mark.anyio
    async def test_void_after_activate(self, client: AsyncClient, auth_setup):
        """激活后作废：所有码项状态变为 revoked"""
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 3,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        void_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=activate-then-void&confirm=void", headers=headers
        )
        assert void_resp.status_code == 200
        assert void_resp.json()["voided"] == 3


class TestCodeItemsAPI:
    """GET /api/v1/code-items 码项列表"""

    @pytest.mark.anyio
    async def test_list_code_items(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}", headers=headers
        )
        assert items_resp.status_code == 200
        data = items_resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5

    @pytest.mark.anyio
    async def test_list_code_items_with_status_filter(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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

        # 激活后过滤 activated
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        activated_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&status=activated",
            headers=headers,
        )
        assert activated_resp.status_code == 200
        assert activated_resp.json()["total"] == 5

        created_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&status=created",
            headers=headers,
        )
        assert created_resp.status_code == 200
        assert created_resp.json()["total"] == 0

    @pytest.mark.anyio
    async def test_list_code_items_pagination(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

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
        batch_id = resp.json()["id"]

        page1 = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}&page=1&page_size=3",
            headers=headers,
        )
        assert page1.status_code == 200
        assert len(page1.json()["items"]) == 3
        assert page1.json()["total"] == 10

    @pytest.mark.anyio
    async def test_get_single_code_item(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 3,
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        item_id = items_resp.json()["items"][0]["id"]

        item_resp = await client.get(
            f"/api/v1/code-items/{item_id}",
            headers=headers,
        )
        assert item_resp.status_code == 200
        assert item_resp.json()["id"] == item_id
        assert item_resp.json()["status"] == "created"


class TestFullCodeLifecycleAPI:
    """完整码生命周期：创建→激活→冻结→作废"""

    @pytest.mark.anyio
    async def test_full_lifecycle(self, client: AsyncClient, auth_setup):
        _, headers, product_id, sku_id, production_batch_id = auth_setup

        # 1. 创建
        create_resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 10,
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        batch_id = create_resp.json()["id"]

        # 验证码项初始状态
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}", headers=headers
        )
        assert items.json()["total"] == 10
        for item in items.json()["items"]:
            assert item["status"] == "created"

        # 2. 激活
        activate_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate", headers=headers
        )
        assert activate_resp.status_code == 200
        assert activate_resp.json()["activated"] == 10

        # 3. 导出 CSV
        export_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/export", headers=headers
        )
        assert export_resp.status_code == 200
        csv_lines = [l for l in export_resp.text.strip().split("\n") if l.strip()]
        assert len(csv_lines) == 11  # header + 10 rows

        # 4. 冻结
        freeze_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/freeze", headers=headers
        )
        assert freeze_resp.status_code == 200
        assert freeze_resp.json()["frozen"] == 10

        # 5. 作废
        void_resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=full-lifecycle-test&confirm=void", headers=headers
        )
        assert void_resp.status_code == 200
        assert void_resp.json()["voided"] == 10

        # 验证所有码项最终状态
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}", headers=headers
        )
        for item in items.json()["items"]:
            assert item["status"] == "revoked"
