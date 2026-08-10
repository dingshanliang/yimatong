"""A6-001: 码解析公开路由验收测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.plan import TenantQuotaUsage
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.services.quota import QUOTA_RECONCILIATION_SOURCE_REVISION
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
        headers=_platform_admin_headers(),
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
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "P-001",
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
            "quantity": 3,
        },
        headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
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

    await client.post(f"/api/v1/code-batches/{batch_id}/export", headers=headers)
    await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "public resolver test", "recipient": "test recipient", "confirm": "deliver"},
        headers=headers,
    )
    await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

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

    @pytest.mark.anyio
    async def test_expired_plan_blocks_scan_and_renewal_immediately_restores_it(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_activated_code,
    ):
        tenant_id, _, _, _, public_id = setup_activated_code
        tenant = await db_session.get(Tenant, uuid.UUID(tenant_id))
        tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.flush()

        expired = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert expired.status_code == 403
        assert expired.json() == {
            "code": "TENANT_PLAN_EXPIRED",
            "detail": "租户套餐已过期，当前仅支持查看；请联系平台续期",
        }

        tenant.plan_expires_at = datetime.now(UTC) + timedelta(days=1)
        await db_session.flush()
        renewed = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert renewed.status_code == 200

    @pytest.mark.anyio
    async def test_expired_plan_returns_safe_browser_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_activated_code,
    ):
        tenant_id, _, _, _, public_id = setup_activated_code
        tenant = await db_session.get(Tenant, uuid.UUID(tenant_id))
        tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.flush()

        response = await client.get(f"/c/{public_id}", headers={"Accept": "text/html"})

        assert response.status_code == 403
        assert response.headers["content-type"].startswith("text/html")
        assert "当前无法继续查验" in response.text
        assert "联系商品品牌方" in response.text
        assert "TENANT_PLAN_EXPIRED" not in response.text
        assert "plan_expires_at" not in response.text

    @pytest.mark.anyio
    async def test_max_scans_charges_successful_repeat_scans_and_blocks_overage(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_activated_code,
    ):
        tenant_id, _, _, _, public_id = setup_activated_code
        tenant_uuid = uuid.UUID(tenant_id)
        tenant = await db_session.get(Tenant, tenant_uuid)
        tenant.quota = {**(tenant.quota or {}), "max_scans": 1}
        usage = await db_session.get(TenantQuotaUsage, tenant_uuid)
        assert usage is not None
        assert usage.enforcement_ready is True
        usage.scans = 0
        usage.reconciled_at = datetime.now(UTC)
        usage.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
        await db_session.flush()

        first = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        second = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json() == {
            "code": "QUOTA_EXCEEDED",
            "detail": "扫码服务额度已用完，请联系品牌方",
        }
        serialized_error = second.text.lower()
        assert "current" not in serialized_error
        assert "limit" not in serialized_error
        assert "max_scans" not in serialized_error
        assert not any(char.isdigit() for char in second.json()["detail"])
        events = await db_session.execute(
            select(ScanEvent).where(ScanEvent.tenant_id == tenant_uuid, ScanEvent.public_id == public_id)
        )
        assert len(list(events.scalars())) == 1

    @pytest.mark.anyio
    async def test_max_scans_returns_safe_browser_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_activated_code,
    ):
        tenant_id, _, _, _, public_id = setup_activated_code
        tenant_uuid = uuid.UUID(tenant_id)
        tenant = await db_session.get(Tenant, tenant_uuid)
        tenant.quota = {**(tenant.quota or {}), "max_scans": 0}
        usage = await db_session.get(TenantQuotaUsage, tenant_uuid)
        assert usage is not None
        assert usage.enforcement_ready is True
        usage.scans = 0
        usage.reconciled_at = datetime.now(UTC)
        usage.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
        await db_session.flush()

        response = await client.get(f"/c/{public_id}", headers={"Accept": "text/html"})

        assert response.status_code == 429
        assert response.headers["content-type"].startswith("text/html")
        assert "当前无法继续查验" in response.text
        assert "联系商品品牌方" in response.text
        assert "max_scans" not in response.text
        assert "QUOTA_EXCEEDED" not in response.text
