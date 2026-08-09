"""Open API 端点测试（/open/v1/*，X-Api-Key 鉴权）。

之前这套端点没有任何专门测试（仅 API Key 管理有测试）。这里用真实
TenantScopeMiddleware + open_api_router，覆盖：鉴权缺失/错误、租户隔离、
各读取端点、券核销（redeem 现在会真正写入 used 状态）。

注意 require_permission 的双重检查：权限必须既在角色的静态契约里，又在
request.state.permissions 里。因此每个测试用对应角色的 ApiKey：
- 读端点：data_reader（含 scan/consumer/claim/event 的 list/detail）
- 券操作：coupon_operator（含 coupon:issue/redeem）
- 产品端点：见 test_open_api_product_endpoints_not_reachable_by_api_key_role
  ——product:create/list/update 不在任何 API Key 角色契约里，这是已知缺口。
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.open_api import open_api_router
from app.middleware.tenant import TenantScopeMiddleware
from app.models.campaign import Benefit, BenefitClaim, Campaign
from app.models.member import ConsumerProfile
from app.models.plan import TenantQuotaUsage
from app.models.product import Brand, Product
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.models.webhook import ApiKey
from tests.conftest import TestSessionLocal


def _seed(api_key: str, role: str, permissions: list[str]):
    """写入一套最小数据，返回关键字段 id。"""

    tenant_id = uuid.uuid4()

    async def _run(db):
        db.add(Tenant(id=tenant_id, name="Open API 测试租户", slug=f"open-{tenant_id.hex[:8]}"))
        db.add(ApiKey(tenant_id=tenant_id, name="外部集成", key=api_key, role=role, permissions=permissions))
        await db.flush()
        db.add(
            ScanEvent(
                tenant_id=tenant_id,
                public_id="TESTPID0001",
                scan_time=datetime.now(UTC),
                is_first_scan=True,
                environment="wechat",
            )
        )
        consumer = ConsumerProfile(tenant_id=tenant_id)
        db.add(consumer)
        brand = Brand(tenant_id=tenant_id, name="测试品牌")
        db.add(brand)
        await db.flush()
        product = Product(tenant_id=tenant_id, brand_id=brand.id, name="测试产品")
        db.add(product)
        await db.flush()
        campaign = Campaign(
            tenant_id=tenant_id,
            name="测试活动",
            campaign_type="coupon",
            start_at="2026-01-01T00:00:00",
            end_at="2027-12-31T23:59:59",
            rules_json={},
        )
        db.add(campaign)
        await db.flush()
        benefit = Benefit(
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            name="测试权益",
            benefit_type="platform_coupon",
            config_json={"url": "https://example.com"},
            stock_total=10,
            stock_used=1,
            per_person_limit=5,
            status="active",
        )
        db.add(benefit)
        await db.flush()
        claim = BenefitClaim(
            tenant_id=tenant_id,
            benefit_id=benefit.id,
            campaign_id=campaign.id,
            consumer_id=str(consumer.id),
            idempotency_key="idem-1",
            status="success",
        )
        db.add(claim)
        await db.commit()
        return {"tenant_id": tenant_id, "claim_id": claim.id}

    return _run


@pytest.fixture
def open_api_app(monkeypatch):
    """挂载真实中间件 + open_api_router 的测试 app，session factory 指向测试 DB。"""

    import app.core.database as database

    monkeypatch.setattr(database, "async_session_factory", TestSessionLocal)
    test_app = FastAPI()
    test_app.include_router(open_api_router)
    test_app.add_middleware(TenantScopeMiddleware)
    return test_app


@pytest.mark.anyio
async def test_open_api_requires_api_key(open_api_app):
    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/open/v1/scans")
        assert resp.status_code == 401  # 缺 X-Api-Key


@pytest.mark.anyio
async def test_open_api_rejects_invalid_key(open_api_app):
    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/open/v1/scans", headers={"X-Api-Key": "bogus"})
        assert resp.status_code == 401


@pytest.mark.anyio
async def test_open_api_read_endpoints_return_tenant_data(open_api_app):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"test-key-{uuid.uuid4()}"
    perms = API_KEY_ROLE_PERMISSIONS["data_reader"]
    async with TestSessionLocal() as db:
        await _seed(api_key, "data_reader", perms)(db)

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Api-Key": api_key}
        for path in ("/open/v1/scans", "/open/v1/consumers", "/open/v1/claims", "/open/v1/events"):
            resp = await client.get(path, headers=headers)
            assert resp.status_code == 200, f"{path}: {resp.text}"
            assert resp.json()["total"] >= 1


@pytest.mark.anyio
async def test_open_api_tenant_isolation(open_api_app):
    """租户 A 的 key 看不到租户 B 的数据。"""
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    perms = API_KEY_ROLE_PERMISSIONS["data_reader"]
    key_a = f"key-a-{uuid.uuid4()}"
    key_b = f"key-b-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(key_a, "data_reader", perms)(db)
        await _seed(key_b, "data_reader", perms)(db)

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/open/v1/scans", headers={"X-Api-Key": key_a})
        assert resp.status_code == 200
        # 每个租户各塞了 1 条；A 只能看到自己的 1 条
        assert resp.json()["total"] == 1


@pytest.mark.anyio
async def test_redeem_coupon_marks_claim_used_and_is_idempotent(open_api_app):
    """核销真正写入 used 状态；重复核销返回 409。"""
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"test-key-{uuid.uuid4()}"
    perms = API_KEY_ROLE_PERMISSIONS["coupon_operator"]
    async with TestSessionLocal() as db:
        ids = await _seed(api_key, "coupon_operator", perms)(db)
    claim_id = ids["claim_id"]

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Api-Key": api_key}
        resp1 = await client.post(f"/open/v1/coupons/{claim_id}/redeem", headers=headers)
        assert resp1.status_code == 200, resp1.text
        assert resp1.json()["status"] == "used"

        async with TestSessionLocal() as db:
            claim = await db.get(BenefitClaim, claim_id)
            assert claim.status == "used"

        resp2 = await client.post(f"/open/v1/coupons/{claim_id}/redeem", headers=headers)
        assert resp2.status_code == 409


@pytest.mark.anyio
async def test_redeem_coupon_cross_tenant_returns_404(open_api_app):
    """租户 A 的 key 核销租户 B 的 claim → 404。"""
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    perms = API_KEY_ROLE_PERMISSIONS["coupon_operator"]
    key_a = f"key-a-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(key_a, "coupon_operator", perms)(db)
        ids_b = await _seed(f"key-b-{uuid.uuid4()}", "coupon_operator", perms)(db)

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/open/v1/coupons/{ids_b['claim_id']}/redeem", headers={"X-Api-Key": key_a})
        assert resp.status_code == 404


@pytest.mark.anyio
async def test_open_api_product_endpoints_require_erp_sync_role(open_api_app):
    """产品/SKU/批次端点（ERP 同步）现在由专用 erp_sync 角色打开。

    防回归点：
    1. erp_sync 精确覆盖 product:list/create/update（ERP 集成的最小权限集）。
    2. 普通读类角色 data_reader 不含 product:* —— 外部分析系统拿不到产品目录，
       避免 ERP 主数据扩散到非集成用途。
    3. full_access 作为 API Key 权限超集，也覆盖 product:*（这是既有约定，见
       test_full_access_has_all），用于运维 break-glass。
    4. 持 erp_sync 角色的 ApiKey 可以真正读取 /open/v1/products。
    """
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    product_perms = {"product:list", "product:create", "product:update"}

    # 1. erp_sync 精确覆盖产品权限
    assert set(API_KEY_ROLE_PERMISSIONS["erp_sync"]) == product_perms

    # 2. data_reader 不能碰产品目录（最小权限）
    assert not (product_perms & set(API_KEY_ROLE_PERMISSIONS["data_reader"]))

    # 3. full_access 超集约定
    assert product_perms.issubset(set(API_KEY_ROLE_PERMISSIONS["full_access"]))

    # 4. erp_sync 的 key 能读取 /open/v1/products
    api_key = f"erp-{uuid.uuid4()}"
    perms = API_KEY_ROLE_PERMISSIONS["erp_sync"]
    async with TestSessionLocal() as db:
        await _seed(api_key, "erp_sync", perms)(db)

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/open/v1/products", headers={"X-Api-Key": api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] >= 1

    # 5. data_reader 的 key 调产品端点仍被拒（最小权限）
    reader_key = f"reader-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(reader_key, "data_reader", API_KEY_ROLE_PERMISSIONS["data_reader"])(db)
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        resp = await client.get("/open/v1/products", headers={"X-Api-Key": reader_key})
        assert resp.status_code == 403


@pytest.mark.anyio
async def test_open_api_erp_sync_can_create_product(open_api_app):
    """erp_sync 角色可以 POST /open/v1/products 创建商品（含幂等 upsert）。"""
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"erp-{uuid.uuid4()}"
    perms = API_KEY_ROLE_PERMISSIONS["erp_sync"]
    async with TestSessionLocal() as db:
        ids = await _seed(api_key, "erp_sync", perms)(db)

    transport = ASGITransport(app=open_api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Product.brand_id 是 NOT NULL，必须带品牌名（_seed 里建了 "测试品牌"）
        body = {"name": "ERP 同步商品", "brand_name": "测试品牌", "external_id": f"ext-{uuid.uuid4().hex[:8]}"}
        resp = await client.post("/open/v1/products", json=body, headers={"X-Api-Key": api_key})
        assert resp.status_code == 201, resp.text
        assert resp.json()["action"] == "created"

        # 同一 external_id 再调一次 → upsert，不报错
        resp2 = await client.post("/open/v1/products", json=body, headers={"X-Api-Key": api_key})
        assert resp2.status_code == 201, resp2.text
        assert resp2.json()["action"] == "updated"

    async with TestSessionLocal() as db:
        usage = await db.get(TenantQuotaUsage, ids["tenant_id"])
        assert usage is not None
        assert usage.products == 1
