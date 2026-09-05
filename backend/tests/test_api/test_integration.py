"""W20: CRM/ERP/商城集成测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.plan import TenantQuotaUsage
from app.models.tenant import Tenant
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "集成测试租户",
            "admin_email": "int@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestBatchImport:
    """W20-001: 批量导入增强"""

    @pytest.mark.anyio
    async def test_batch_import_products(self, client: AsyncClient, setup_tenant, db_session: AsyncSession):
        tid, headers = setup_tenant
        # 创建品牌
        brand_resp = await client.post(
            "/api/v1/brands",
            json={"name": "集成测试品牌"},
            headers=headers,
        )
        brand_id = brand_resp.json()["id"]

        resp = await client.post(
            "/api/v1/integration/batch-import",
            json={
                "type": "products",
                "items": [
                    {"brand_id": brand_id, "name": "批量产品1", "category": "水果"},
                    {"brand_id": brand_id, "name": "批量产品2", "category": "水果"},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] == 2
        tenant_id = uuid.UUID(tid)
        usage = await db_session.scalar(select(TenantQuotaUsage).where(TenantQuotaUsage.tenant_id == tenant_id))
        assert usage is not None
        assert usage.products == 2

    @pytest.mark.anyio
    async def test_batch_import_reserves_all_products_atomically(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        tid, headers = setup_tenant
        tenant_id = uuid.UUID(tid)
        tenant = await db_session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        tenant.quota = {"max_products": 1}
        brand_resp = await client.post("/api/v1/brands", json={"name": "限额品牌"}, headers=headers)
        brand_id = brand_resp.json()["id"]

        response = await client.post(
            "/api/v1/integration/batch-import",
            json={
                "type": "products",
                "items": [
                    {"brand_id": brand_id, "name": "产品1"},
                    {"brand_id": brand_id, "name": "产品2"},
                ],
            },
            headers=headers,
        )
        assert response.status_code == 429
        assert response.json()["error_code"] == "QUOTA_EXCEEDED"
        usage = await db_session.scalar(select(TenantQuotaUsage).where(TenantQuotaUsage.tenant_id == tenant_id))
        assert usage is not None
        assert usage.products == 0


class TestErpSync:
    """W20-002: ERP 进销存同步"""

    @pytest.mark.anyio
    async def test_inventory_sync(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/integration/erp/inventory-sync",
            json={
                "records": [
                    {"batch_code": "BATCH001", "direction": "out", "quantity": 100, "destination": "华东仓库"},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["synced"] == 1


class TestCrmSync:
    """W20-003: CRM 客户同步"""

    @pytest.mark.anyio
    async def test_customer_sync(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/integration/crm/customer-sync",
            json={
                "customers": [
                    {"external_id": "CRM001", "phone_hash": "hash1", "name": "客户A"},
                    {"external_id": "CRM002", "phone_hash": "hash2", "name": "客户B"},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["synced"] == 2


_INTEGRATION_WRITE_CASES = [
    (
        "/api/v1/integration/batch-import",
        "product:create",
        {"type": "products", "items": [{"brand_id": "00000000-0000-0000-0000-0000000000aa", "name": "产品A"}]},
    ),
    (
        "/api/v1/integration/erp/inventory-sync",
        "product:update",
        {"records": [{"batch_code": "BATCH001", "direction": "out", "quantity": 1}]},
    ),
    (
        "/api/v1/integration/crm/customer-sync",
        "consumer:detail",
        {"customers": [{"external_id": "CRM001", "phone_hash": "hash1"}]},
    ),
]


class TestIntegrationPermissions:
    """回归：integration 写端点必须经过 RBAC 权限校验（历史上完全裸奔）。"""

    @pytest.mark.anyio
    @pytest.mark.parametrize(("path", "permission", "payload"), _INTEGRATION_WRITE_CASES)
    async def test_viewer_rejected_for_missing_permission(
        self, client: AsyncClient, path: str, permission: str, payload: dict
    ):
        """无权限角色（viewer）必须 403，且报缺失的权限码。"""
        token = create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), "viewer")
        resp = await client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == f"Missing permission: {permission}"

    @pytest.mark.anyio
    @pytest.mark.parametrize(("path", "_permission", "payload"), _INTEGRATION_WRITE_CASES)
    async def test_admin_passes_permission_layer_to_service(
        self, client: AsyncClient, path: str, _permission: str, payload: dict, monkeypatch: pytest.MonkeyPatch
    ):
        """admin 必须能穿过权限层进入业务逻辑（对照 /imports/excel 与 members/consumers 同码）。"""

        async def _fake_service(_db, _tenant_id, _items):
            return 42

        monkeypatch.setattr("app.api.v1.integration.batch_import_products", _fake_service)
        monkeypatch.setattr("app.api.v1.integration.sync_inventory", _fake_service)
        monkeypatch.setattr("app.api.v1.integration.sync_customers", _fake_service)

        token = create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), "admin")
        resp = await client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() in ({"imported": 42}, {"synced": 42})
