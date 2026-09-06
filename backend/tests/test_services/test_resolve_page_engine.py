"""A6-002: 码解析对接页面引擎测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


async def _deliver_and_activate_batch(client: AsyncClient, headers: dict, batch_id: str) -> None:
    exported = await client.post(
        f"/api/v1/code-batches/{batch_id}/export",
        json={"reason": "Test lifecycle setup"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    delivered = await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "page resolver fixture", "recipient": "page resolver tests", "confirm": "deliver"},
        headers=headers,
    )
    activated = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert [exported.status_code, printing.status_code, delivered.status_code, activated.status_code] == [
        200,
        200,
        200,
        200,
    ]


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
async def full_setup(client: AsyncClient):
    """创建完整链路：租户→品牌→产品→SKU→码批次→模板→版本→发布"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "引擎测试",
            "admin_email": "engine@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "引擎品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "引擎产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "E-SKU", "name": "E SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]

    # 创建生产批次（码批次需要 production_batch_id）
    pb = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "E-001",
            "production_date": "2024-01-01",
            "expiry_date": "2027-01-01",
        },
        headers=headers,
    )
    production_batch_id = pb.json()["id"]

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch_id,
            "quantity": 2,
        },
        headers={
            **headers,
            "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"page-engine:{tid}")),
        },
    )
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]

    # 创建页面模板（关联产品）
    tmpl = await client.post(
        "/api/v1/page-templates",
        json={
            "name": "引擎模板",
            "template_type": "product_info",
            "product_id": product_id,
        },
        headers=headers,
    )
    template_id = tmpl.json()["id"]

    # 创建版本并发布
    ver = await client.post(
        f"/api/v1/page-templates/{template_id}/versions",
        json={
            "config_json": {
                "brand_name": "引擎品牌",
                "product_name": "引擎产品",
            }
        },
        headers=headers,
    )
    vid = ver.json()["id"]
    await client.post(f"/api/v1/page-versions/{vid}/publish", headers=headers)

    # 完成权威交付链后激活码
    await _deliver_and_activate_batch(client, headers, batch_id)

    # 模板渲染经由上线版本生效：创建活动并发布 launch release
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "引擎活动",
            "campaign_type": "scan",
            "product_id": product_id,
            "start_at": "2026-01-01T00:00:00+00:00",
            "end_at": "2030-12-31T00:00:00+00:00",
            "rules_json": {
                "participation_conditions": "扫码参与",
                "claim_limits": "限1次",
                "validity_period": "7天",
                "disclaimer": "品牌方保留解释权",
                "minor_notice": "需监护人陪同",
                "customer_service_contact": "400-000-0000",
            },
        },
        headers=headers,
    )
    assert campaign.status_code == 201, campaign.text
    campaign_id = campaign.json()["id"]
    benefit = await client.post(
        f"/api/v1/campaigns/{campaign_id}/benefits",
        json={
            "name": "引擎权益",
            "benefit_type": "external_link",
            "config_json": {"url": "https://example.com/landing"},
            "stock_total": 10,
        },
        headers=headers,
    )
    assert benefit.status_code == 201, benefit.text
    activated = await client.post(
        f"/api/v1/campaigns/{campaign_id}/status",
        json={"status": "active"},
        headers=headers,
    )
    assert activated.status_code == 200, activated.text
    created = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": vid,
            "campaign_id": campaign_id,
            "code_batch_id": batch_id,
            "idempotency_key": f"page-engine-release:{tid}",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    release_id = created.json()["id"]
    confirmed = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm",
        json={"idempotency_key": f"page-engine-confirm:{tid}"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    launched = await client.post(
        f"/api/v1/launch-releases/{release_id}/launch",
        json={"idempotency_key": f"page-engine-launch:{tid}"},
        headers=headers,
    )
    assert launched.status_code == 200, launched.text

    # 获取第一个码的 public_id
    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}&page_size=1",
        headers=headers,
    )
    public_id = items.json()["items"][0]["public_id"]

    return tid, headers, public_id


class TestResolvePageEngine:
    @pytest.mark.anyio
    async def test_resolve_renders_template(self, client: AsyncClient, full_setup):
        _, _, public_id = full_setup
        # 微信 UA：落地页模板渲染按微信场景设计；非微信 UA 走引导页（见 resolver）
        resp = await client.get(
            f"/c/{public_id}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone) MicroMessenger/8.0"},
        )
        assert resp.status_code == 200
        assert "引擎品牌" in resp.text
        assert "引擎产品" in resp.text

    @pytest.mark.anyio
    async def test_resolve_no_template_returns_default(self, client: AsyncClient):
        """没有关联模板时返回默认页面"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "无模板",
                "admin_email": "notmpl@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tid = resp.json()["id"]
        token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Authorization": f"Bearer {token}"}

        brand = await client.post("/api/v1/brands", json={"name": "无模板品牌"}, headers=headers)
        prod = await client.post(
            "/api/v1/products",
            json={"brand_id": brand.json()["id"], "name": "无模板产品"},
            headers=headers,
        )
        sku = await client.post(
            "/api/v1/skus",
            json={"product_id": prod.json()["id"], "code": "NT-SKU", "name": "NT SKU"},
            headers=headers,
        )
        production_batch = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": prod.json()["id"],
                "sku_id": sku.json()["id"],
                "batch_code": "NT-PB-001",
                "production_date": "2026-07-01",
                "expiry_date": "2027-07-01",
            },
            headers=headers,
        )
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": prod.json()["id"],
                "sku_id": sku.json()["id"],
                "production_batch_id": production_batch.json()["id"],
                "batch_code": "NT-001",
                "quantity": 1,
            },
            headers={
                **headers,
                "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"page-engine-default:{tid}")),
            },
        )
        assert batch.status_code == 201, batch.text
        await _deliver_and_activate_batch(client, headers, batch.json()["id"])
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch.json()['id']}",
            headers=headers,
        )
        public_id = items.json()["items"][0]["public_id"]

        resp = await client.get(
            f"/c/{public_id}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone) MicroMessenger/8.0"},
        )
        assert resp.status_code == 200
        assert "产品信息" in resp.text
