"""验证 resolver JSON 响应结构完整性"""

import uuid
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.code import CodeBatch, CodeBatchStatus
from app.models.product import ProductionBatch
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
async def db_session():
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
async def traceability_setup(client: AsyncClient):
    """创建带溯源数据的完整链路"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "溯源测试",
            "admin_email": "trace@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code in (200, 201)
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "溯源品牌"}, headers=headers)
    assert brand.status_code in (200, 201)

    prod = await client.post(
        "/api/v1/products",
        json={
            "brand_id": brand.json()["id"],
            "name": "溯源大米",
            "description": "测试溯源",
            "origin": "黑龙江五常",
            "image_url": "https://example.com/rice.jpg",
        },
        headers=headers,
    )
    assert prod.status_code in (200, 201)

    sku = await client.post(
        "/api/v1/skus",
        json={
            "product_id": prod.json()["id"],
            "code": "TRACE-SKU",
            "name": "5kg装",
        },
        headers=headers,
    )
    assert sku.status_code in (200, 201)

    pb = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "PB-TRACE-001",
            "production_date": "2026-03-01",
            "expiry_date": "2027-03-01",
            "origin": "黑龙江省五常市",
        },
        headers=headers,
    )
    assert pb.status_code in (200, 201)

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": pb.json()["id"],
            "batch_code": "CB-TRACE-001",
            "quantity": 2,
        },
        headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
    )
    assert batch.status_code in (200, 201)

    batch_id = batch.json()["id"]
    exported = await client.post(f"/api/v1/code-batches/{batch_id}/export", headers=headers)
    printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    delivered = await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "resolver test", "recipient": "test recipient", "confirm": "deliver"},
        headers=headers,
    )
    activated = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert exported.status_code == printing.status_code == delivered.status_code == activated.status_code == 200

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch.json()['id']}",
        headers=headers,
    )
    return items.json()["items"][0]["public_id"]


class TestResolverJsonResponse:
    @pytest.mark.anyio
    async def test_item_status_cannot_bypass_unactivated_parent_batch(
        self,
        client,
        traceability_setup,
        db_session,
    ):
        code_batch = await db_session.scalar(select(CodeBatch))
        code_batch.status = CodeBatchStatus.delivered
        await db_session.flush()

        response = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})

        assert response.status_code == 404
        assert "scan_token" not in response.json()

    @pytest.mark.anyio
    async def test_recalled_production_batch_keeps_traceability_but_blocks_benefits(self, client, traceability_setup):
        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tenant_id = tenant_resp.json()["items"][0]["id"]
        headers = {
            "Authorization": f"Bearer {create_access_token(tenant_id, '00000000-0000-0000-0000-000000000001', 'admin')}"
        }
        batches = await client.get("/api/v1/production-batches", headers=headers)
        batch_id = batches.json()["items"][0]["id"]
        recalled = await client.post(
            f"/api/v1/production-batches/{batch_id}/recall",
            json={"reason": "检测结果异常", "confirm": "recall"},
            headers=headers,
        )
        assert recalled.status_code == 200

        response = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})

        assert response.status_code == 200
        payload = response.json()
        assert payload.get("scan_token") is None
        assert payload["code_data"]["batch"]["status"] == "recalled"
        assert payload["code_data"]["batch"]["recall_reason"] == "检测结果异常"
        assert payload["scan_info"]["paused_reason"] == "production_batch_recalled"
        assert payload["scan_info"]["recall_warning"]["reason"] == "检测结果异常"
        assert "campaign" not in payload

    @pytest.mark.anyio
    async def test_expired_production_batch_keeps_traceability_without_scan_token(
        self, client, traceability_setup, db_session
    ):
        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tenant_id = tenant_resp.json()["items"][0]["id"]
        batch = (
            await db_session.execute(select(ProductionBatch).where(ProductionBatch.tenant_id == uuid.UUID(tenant_id)))
        ).scalar_one()
        batch.expiry_date = date.today() - timedelta(days=1)
        await db_session.flush()

        response = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})

        assert response.status_code == 200
        payload = response.json()
        assert payload.get("scan_token") is None
        assert payload["code_data"]["batch"]["status"] == "expired"
        assert payload["scan_info"]["paused_reason"] == "production_batch_expired"
        assert "campaign" not in payload

    @pytest.mark.anyio
    async def test_brand_identity_update_invalidates_warm_public_product_cache(self, client, traceability_setup):
        first = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})
        assert first.status_code == 200
        assert first.json()["brand"]["name"] == "溯源品牌"

        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tenant_id = tenant_resp.json()["items"][0]["id"]
        headers = {
            "Authorization": f"Bearer {create_access_token(tenant_id, '00000000-0000-0000-0000-000000000001', 'admin')}"
        }
        brands = await client.get("/api/v1/brands", headers=headers)
        brand_id = brands.json()["items"][0]["id"]
        updated = await client.patch(
            f"/api/v1/brands/{brand_id}",
            json={"name": "即时更新品牌", "logo_url": "https://assets.example.com/brand.png"},
            headers=headers,
        )
        assert updated.status_code == 200

        second = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})
        assert second.status_code == 200
        assert second.json()["brand"] == {
            "name": "即时更新品牌",
            "logo_url": "https://assets.example.com/brand.png",
        }

    @pytest.mark.anyio
    async def test_json_has_product_image_url(self, client, traceability_setup):
        """产品图片应该用 image_url 而非空 images 数组"""
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        product = data["code_data"]["product"]
        assert "image_url" in product, "缺少 image_url 字段"
        assert product["image_url"] == "https://example.com/rice.jpg"

    @pytest.mark.anyio
    async def test_json_has_traceability_data(self, client, traceability_setup):
        """响应应包含溯源信息（产地、生产日期、保质期、批次号）"""
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        batch = data.get("batch") or data.get("code_data", {}).get("batch")
        assert batch is not None, "缺少溯源/批次信息"
        assert batch.get("batch_code") == "PB-TRACE-001"
        assert batch.get("origin") == "黑龙江省五常市"
        assert batch.get("production_date") is not None
        assert batch.get("expiry_date") is not None

    @pytest.mark.anyio
    async def test_first_scan_count(self, client, traceability_setup):
        """yimatong-zgb1.4：scan_count 改为 post-insert 语义（含本次），
        首次查验 = 1（旧 0 语义已废弃）。``verification_count`` 是新契约字段，
        与 scan_count 同值（scan_count 兼容别名）。
        """
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        assert data["scan_info"]["is_first_scan"] is True
        # post-insert 语义：含本次，首次 = 1
        assert data["scan_info"]["scan_count"] == 1, "首次查验 scan_count 应为 1（含本次）"
        # 新契约字段，与 scan_count 同值
        assert data["scan_info"]["verification_count"] == 1
        # 新增首查时间字段，读自 code_items.first_scanned_at
        assert data["scan_info"]["first_scan_time"] is not None
        assert data["scan_info"]["verification_time"] is not None

    @pytest.mark.anyio
    async def test_tenant_brand_profile_logo_injected(self, client, traceability_setup, db_session):
        """ADR-0001：租户 brand_profile.logo_url 注入 tenant_branding，
        且优先于产品品牌 logo（品牌方自助配置必须生效）。"""
        # traceability_setup 已建租户 + 产品品牌（无 logo）。先给租户设 brand_profile
        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tid = tenant_resp.json()["items"][0]["id"]
        tenant_headers = {
            "Authorization": f"Bearer {create_access_token(tid, '00000000-0000-0000-0000-000000000001', 'admin')}"
        }
        await client.patch(
            "/api/v1/tenants/me",
            json={"brand_profile": {"logo_url": "https://cdn.example.com/tenant-logo.png"}},
            headers=tenant_headers,
        )

        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        branding = data.get("tenant_branding") or {}
        assert branding.get("logo_url") == "https://cdn.example.com/tenant-logo.png", (
            "租户 brand_profile.logo_url 必须注入 tenant_branding"
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "historical_logo",
        [
            "https://127.0.0.1/internal.png",
            "/api/v1/files/public/a/../../../auth/logout",
            "/api/v1/files/public/a/%2e%2e/%2e%2e/auth/logout",
            42,
        ],
    )
    async def test_historical_unsafe_tenant_logo_is_not_published(
        self, client, traceability_setup, db_session, historical_logo
    ):
        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tenant = await db_session.get(Tenant, uuid.UUID(tenant_resp.json()["items"][0]["id"]))
        assert tenant is not None
        tenant.brand_profile = {"logo_url": historical_logo}
        await db_session.flush()

        response = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})

        assert response.status_code == 200
        assert (response.json().get("tenant_branding") or {}).get("logo_url") in (None, "")

    @pytest.mark.anyio
    async def test_public_response_does_not_honor_unentitled_historical_white_label(
        self, client, traceability_setup, db_session
    ):
        tenant_resp = await client.get("/api/v1/tenants", headers=_platform_admin_headers())
        tenant = await db_session.get(Tenant, uuid.UUID(tenant_resp.json()["items"][0]["id"]))
        assert tenant is not None
        tenant.brand_profile = {"hide_yimatong_brand": True}
        tenant.enabled_features = {}
        await db_session.flush()

        response = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        assert response.json()["tenant_branding"]["hide_yimatong_brand"] is False
