"""Open API 端点测试（/open/v1/*，X-Api-Key 鉴权）。

之前这套端点没有任何专门测试（仅 API Key 管理有测试）。这里用真实
TenantScopeMiddleware + open_api_router，覆盖：鉴权缺失/错误、租户隔离、
各读取端点、券核销（redeem 现在会真正写入 used 状态）。

注意 require_permission 的双重检查：权限必须既在角色的静态契约里，又在
request.state.permissions 里。因此每个测试用对应角色的 ApiKey：
- 读端点：data_reader（含 scan/consumer/claim/event 的 list/detail）
- 券操作：coupon_operator（含 coupon:issue/redeem）
- 产品端点：erp_sync（精确 product:list/create/update），full_access 为运维超集。
"""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1.open_api import open_api_router
from app.middleware.tenant import TenantScopeMiddleware
from app.models.campaign import Benefit, BenefitClaim, Campaign
from app.models.member import ConsumerProfile
from app.models.plan import TenantQuotaUsage
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.scan import ScanEvent
from app.models.tenant import Tenant, TenantStatus, TenantType
from app.models.webhook import ApiKey
from tests.conftest import TestSessionLocal


def _seed(
    api_key: str,
    role: str,
    permissions: list[str],
    *,
    tenant_status: TenantStatus = TenantStatus.active,
    tenant_type: TenantType = TenantType.brand,
    revoked: bool = False,
    expires_at: datetime | None = None,
):
    """写入一套最小数据，返回关键字段 id。"""

    tenant_id = uuid.uuid4()

    async def _run(db):
        db.add(
            Tenant(
                id=tenant_id,
                name="Open API 测试租户",
                slug=f"open-{tenant_id.hex[:8]}",
                status=tenant_status,
                tenant_type=tenant_type,
            )
        )
        db.add(
            ApiKey(
                tenant_id=tenant_id,
                name="外部集成",
                key_prefix=api_key[:12],
                key_digest=hashlib.sha256(api_key.encode()).hexdigest(),
                role=role,
                permissions=permissions,
                revoked=revoked,
                revoked_at=datetime.now(UTC) if revoked else None,
                expires_at=expires_at,
            )
        )
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
        return {
            "tenant_id": tenant_id,
            "claim_id": claim.id,
            "brand_id": brand.id,
            "product_id": product.id,
        }

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
async def test_open_api_authentication_failures_are_uniform(open_api_app):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    permissions = API_KEY_ROLE_PERMISSIONS["data_reader"]
    cases = [
        (f"revoked-{uuid.uuid4()}", {"revoked": True}),
        (f"expired-{uuid.uuid4()}", {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
        (f"suspended-{uuid.uuid4()}", {"tenant_status": TenantStatus.suspended}),
        (f"agency-{uuid.uuid4()}", {"tenant_type": TenantType.agency}),
    ]
    async with TestSessionLocal() as db:
        for api_key, options in cases:
            await _seed(api_key, "data_reader", permissions, **options)(db)

    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        responses = [await client.get("/open/v1/scans")]
        responses.append(await client.get("/open/v1/scans", headers={"X-Api-Key": "invalid"}))
        for api_key, _ in cases:
            responses.append(await client.get("/open/v1/scans", headers={"X-Api-Key": api_key}))

    assert {(response.status_code, response.json().get("detail")) for response in responses} == {
        (401, "Invalid API key")
    }


@pytest.mark.anyio
async def test_open_api_x_api_key_precedes_bearer_and_cookie(open_api_app):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"precedence-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(api_key, "data_reader", API_KEY_ROLE_PERMISSIONS["data_reader"])(db)

    headers = {
        "X-Api-Key": api_key,
        "Authorization": "Bearer invalid-bearer",
        "Cookie": "access_token=invalid-cookie",
    }
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        valid_key = await client.get("/open/v1/scans", headers=headers)
        invalid_key = await client.get(
            "/open/v1/scans",
            headers={**headers, "X-Api-Key": "invalid-key"},
        )

    assert valid_key.status_code == 200
    assert invalid_key.status_code == 401


@pytest.mark.anyio
async def test_open_api_rate_limit_fails_closed(open_api_app, shared_security_cache):
    shared_security_cache.fail_rate_limits = True

    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        resp = await client.get("/open/v1/scans", headers={"X-Api-Key": "untrusted-key"})

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_open_api_rate_limit_returns_retry_after(open_api_app, monkeypatch):
    from app.services.redis_cache import AsyncRedisCache

    async def reject(*_args, **_kwargs):
        return False, 0

    monkeypatch.setattr(AsyncRedisCache, "rate_limit_check_shared", reject)
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        resp = await client.get("/open/v1/scans", headers={"X-Api-Key": "untrusted-key"})

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"


@pytest.mark.anyio
async def test_open_api_rate_limit_keys_never_contain_the_secret(open_api_app, shared_security_cache):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"secret-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(api_key, "data_reader", API_KEY_ROLE_PERMISSIONS["data_reader"])(db)

    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        resp = await client.get("/open/v1/scans", headers={"X-Api-Key": api_key})

    assert resp.status_code == 200
    assert len(shared_security_cache.rate_keys) == 3
    assert all(api_key not in key for key in shared_security_cache.rate_keys)
    assert any(key.startswith("global:") for key in shared_security_cache.rate_keys)
    assert any(key.startswith("ip:") for key in shared_security_cache.rate_keys)
    assert any(key.startswith("key:") for key in shared_security_cache.rate_keys)


@pytest.mark.anyio
async def test_invalid_api_keys_do_not_create_unbounded_per_key_rate_buckets(open_api_app, shared_security_cache):
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        responses = [
            await client.get("/open/v1/scans", headers={"X-Api-Key": f"random-{uuid.uuid4()}"}) for _ in range(25)
        ]

    assert {response.status_code for response in responses} == {401}
    assert not any(key.startswith("key:") for key in shared_security_cache.rate_keys)
    assert {key.split(":", 1)[0] for key in shared_security_cache.rate_keys} == {"global", "ip"}


@pytest.mark.anyio
async def test_open_api_ip_limit_ignores_caller_supplied_forwarding_headers(
    open_api_app,
    shared_security_cache,
    caplog,
):
    spoofed_addresses = [f"198.51.100.{index % 250 + 1}" for index in range(301)]
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        responses = [
            await client.get(
                "/open/v1/scans",
                headers={
                    "X-Api-Key": "invalid",
                    "X-Real-IP": spoofed_ip,
                    "X-Forwarded-For": f"{spoofed_ip}, 203.0.113.10",
                },
            )
            for spoofed_ip in spoofed_addresses
        ]

    assert {response.status_code for response in responses[:300]} == {401}
    assert responses[300].status_code == 429
    ip_buckets = {key for key in shared_security_cache.rate_keys if key.startswith("ip:")}
    assert len(ip_buckets) == 1
    cache_and_log_evidence = " ".join([*shared_security_cache.rate_keys, caplog.text])
    assert "127.0.0.1" not in cache_and_log_evidence
    assert all(address not in cache_and_log_evidence for address in spoofed_addresses)


@pytest.mark.anyio
async def test_valid_api_key_has_a_stable_shared_rate_bucket(open_api_app, shared_security_cache):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"secret-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        await _seed(api_key, "data_reader", API_KEY_ROLE_PERMISSIONS["data_reader"])(db)

    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        first = await client.get("/open/v1/scans", headers={"X-Api-Key": api_key})
        key_bucket = next(key for key in shared_security_cache.rate_keys if key.startswith("key:"))
        shared_security_cache.rate_counts[key_bucket] = 600
        blocked = await client.get("/open/v1/scans", headers={"X-Api-Key": api_key})

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"
    assert api_key not in key_bucket


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


@pytest.mark.anyio
async def test_open_api_compatibility_names_fail_closed_when_ambiguous(open_api_app):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"erp-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        ids = await _seed(api_key, "erp_sync", API_KEY_ROLE_PERMISSIONS["erp_sync"])(db)
        duplicate_product = Product(
            tenant_id=ids["tenant_id"],
            brand_id=ids["brand_id"],
            name="测试产品",
        )
        db.add(duplicate_product)
        await db.flush()
        first_sku = SKU(tenant_id=ids["tenant_id"], product_id=ids["product_id"], code="SHARED-CODE", name="规格一")
        second_sku = SKU(tenant_id=ids["tenant_id"], product_id=duplicate_product.id, code="SHARED-CODE", name="规格二")
        db.add_all([first_sku, second_sku])
        await db.commit()

    headers = {"X-Api-Key": api_key}
    async with AsyncClient(transport=ASGITransport(app=open_api_app), base_url="http://test") as client:
        ambiguous_product = await client.post(
            "/open/v1/skus",
            json={"product_name": "测试产品", "code": "NEW-SKU", "name": "新规格"},
            headers=headers,
        )
        assert ambiguous_product.status_code == 409

        exact_product = await client.post(
            "/open/v1/skus",
            json={"product_id": str(ids["product_id"]), "code": "NEW-SKU", "name": "新规格"},
            headers=headers,
        )
        assert exact_product.status_code == 201

        ambiguous_sku = await client.post(
            "/open/v1/batches",
            json={
                "sku_code": "SHARED-CODE",
                "batch_code": "AMBIGUOUS-BATCH",
                "production_date": "2026-08-10",
                "expiry_date": "2027-08-10",
            },
            headers=headers,
        )
        assert ambiguous_sku.status_code == 409

        exact_sku = await client.post(
            "/open/v1/batches",
            json={
                "sku_id": str(first_sku.id),
                "batch_code": "EXACT-BATCH",
                "production_date": "2026-08-10",
                "expiry_date": "2027-08-10",
                "origin": "黑龙江省五常市",
                "external_id": "ERP-BATCH-001",
            },
            headers=headers,
        )
        assert exact_sku.status_code == 201
        assert exact_sku.json()["product_id"] == str(first_sku.product_id)
        assert exact_sku.json()["sku_id"] == str(first_sku.id)
        assert exact_sku.json()["origin"] == "黑龙江省五常市"

        updated_origin = await client.post(
            "/open/v1/batches",
            json={
                "sku_id": str(first_sku.id),
                "batch_code": "EXACT-BATCH",
                "production_date": "2026-08-10",
                "expiry_date": "2027-08-10",
                "origin": "黑龙江省哈尔滨市五常市",
                "external_id": "ERP-BATCH-001",
            },
            headers=headers,
        )
        assert updated_origin.status_code == 201
        assert updated_origin.json()["action"] == "updated"
        assert updated_origin.json()["origin"] == "黑龙江省哈尔滨市五常市"

        mismatched_upsert = await client.post(
            "/open/v1/batches",
            json={
                "sku_id": str(second_sku.id),
                "batch_code": "MUST-NOT-RETARGET",
                "production_date": "2026-08-11",
                "expiry_date": "2027-08-11",
                "external_id": "ERP-BATCH-001",
            },
            headers=headers,
        )
        assert mismatched_upsert.status_code == 409
        assert mismatched_upsert.json()["detail"]["authoritative_product_id"] == str(first_sku.product_id)
        assert mismatched_upsert.json()["detail"]["authoritative_sku_id"] == str(first_sku.id)

        async with TestSessionLocal() as db:
            persisted = await db.scalar(
                select(ProductionBatch).where(
                    ProductionBatch.tenant_id == ids["tenant_id"],
                    ProductionBatch.external_id == "ERP-BATCH-001",
                )
            )
            assert persisted.origin == "黑龙江省哈尔滨市五常市"
            persisted.production_date = date.today() - timedelta(days=2)
            persisted.expiry_date = date.today() - timedelta(days=1)
            await db.commit()

        immutable = await client.post(
            "/open/v1/batches",
            json={
                "sku_id": str(first_sku.id),
                "batch_code": "MUST-STAY-IMMUTABLE",
                "production_date": "2026-08-12",
                "expiry_date": "2027-08-12",
                "external_id": "ERP-BATCH-001",
            },
            headers=headers,
        )
        assert immutable.status_code == 409
        assert immutable.json()["detail"]["code"] == "production_batch_not_active"
        assert immutable.json()["detail"]["status"] == "expired"


@pytest.mark.anyio
async def test_open_api_catalog_write_rolls_back_when_authenticated_audit_fails(open_api_app, monkeypatch):
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS

    api_key = f"erp-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        ids = await _seed(api_key, "erp_sync", API_KEY_ROLE_PERMISSIONS["erp_sync"])(db)

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.api.v1.open_api.write_audit_log", fail_audit)
    external_id = f"rollback-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(
        transport=ASGITransport(app=open_api_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/open/v1/products",
            json={
                "name": "必须回滚的产品",
                "brand_id": str(ids["brand_id"]),
                "external_id": external_id,
            },
            headers={"X-Api-Key": api_key},
        )
    assert response.status_code == 500

    async with TestSessionLocal() as db:
        persisted = await db.execute(
            select(Product.id).where(Product.tenant_id == ids["tenant_id"], Product.external_id == external_id)
        )
        assert persisted.scalar_one_or_none() is None
