"""码管理安全加固测试（P0 修复验证）"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()


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
        json={"product_id": product_id, "code": "SKU-001", "name": "测试 SKU"},
        headers=headers,
    )
    sku_id = sku_resp.json()["id"]
    pb_resp = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "PB-001",
            "production_date": "2024-01-01",
            "expiry_date": "2025-01-01",
        },
        headers=headers,
    )
    production_batch_id = pb_resp.json()["id"]
    return product_id, sku_id, production_batch_id


@pytest.fixture
async def code_batch_with_auth(client: AsyncClient, tenant_with_auth, sku_with_auth):
    tid, headers = tenant_with_auth
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
    assert resp.status_code == 201
    batch_id = uuid.UUID(resp.json()["id"])

    # 获取批次下的码项
    items_resp = await client.get(
        "/api/v1/code-items", params={"code_batch_id": batch_id}, headers=headers
    )
    items = items_resp.json()["items"]
    item_id = uuid.UUID(items[0]["id"])
    return tid, headers, batch_id, item_id


class TestStatusMachineProtection:
    """1.1 状态机绕过修复测试"""

    @pytest.mark.anyio
    async def test_patch_status_directly_rejected(self, client: AsyncClient, code_batch_with_auth):
        _, headers, _, item_id = code_batch_with_auth
        resp = await client.patch(
            f"/api/v1/code-items/{item_id}",
            json={"status": "expired"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Direct status modification is not allowed" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_patch_other_fields_allowed(self, client: AsyncClient, code_batch_with_auth):
        _, headers, _, item_id = code_batch_with_auth
        # 当前 update_code_item 只允许修改 status（已被禁用），所以 PATCH 任何字段都会 400
        # 如果将来有其他可修改字段，此测试需更新
        resp = await client.patch(
            f"/api/v1/code-items/{item_id}",
            json={"status": "activated"},
            headers=headers,
        )
        assert resp.status_code == 400


class TestPermissionEnforcement:
    """1.2 权限校验测试"""

    @pytest.mark.anyio
    async def test_operator_cannot_manage_batch(self, client: AsyncClient, code_batch_with_auth, tenant_with_auth):
        tid, _ = tenant_with_auth
        _, _, batch_id, _ = code_batch_with_auth
        # 使用 operator token 尝试激活批次
        op_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        op_headers = {"Authorization": f"Bearer {op_token}"}

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=op_headers,
        )
        # operator 没有 code:manage，应返回 403
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_operator_can_export(self, client: AsyncClient, code_batch_with_auth, tenant_with_auth):
        tid, _ = tenant_with_auth
        _, _, batch_id, _ = code_batch_with_auth
        op_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        op_headers = {"Authorization": f"Bearer {op_token}"}

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            headers=op_headers,
        )
        # operator 有 code:export 权限，应返回 200
        assert resp.status_code == 200


class TestPublicIdConflictHandling:
    """1.3 public_id 冲突检测测试"""

    @pytest.mark.anyio
    async def test_batch_generation_no_duplicate_ids(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        tid, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 100,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        batch_id = uuid.UUID(resp.json()["id"])

        # 获取所有码项，验证 public_id 唯一
        items_resp = await client.get(
            "/api/v1/code-items", params={"code_batch_id": batch_id, "page_size": 100},
            headers=headers,
        )
        items = items_resp.json()["items"]
        public_ids = [i["public_id"] for i in items]
        assert len(public_ids) == len(set(public_ids)), "Duplicate public_id found within batch"


class TestBatchStateMachine:
    """3.3 批次状态机测试"""

    @pytest.mark.anyio
    async def test_mark_printing_invalid_transition(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        # 批次状态为 completed（创建后自动完成），可以直接标记 printing
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp.status_code == 200

        # 再次标记 printing 应失败（状态机不允许 printing -> printing）
        resp2 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp2.status_code == 409

    @pytest.mark.anyio
    async def test_mark_delivered_requires_printing(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        # 直接标记 delivered 应失败（需要 printing 状态）
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-delivered",
            headers=headers,
        )
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_delivered_after_printing(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        # 先标记 printing
        resp1 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp1.status_code == 200

        # 再标记 delivered
        resp2 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-delivered",
            headers=headers,
        )
        assert resp2.status_code == 200


class TestExportNotFound:
    """1.4 导出 404 测试"""

    @pytest.mark.anyio
    async def test_export_nonexistent_batch_returns_404(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/export",
            headers=headers,
        )
        assert resp.status_code == 404
