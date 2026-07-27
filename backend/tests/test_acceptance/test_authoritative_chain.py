"""yimatong-zgb1.2 验收门禁 — 消费者只看到权威产品与生产批次资料。

在真实 PostgreSQL 上证明：
1. 消费者解析码返回权威批次字段（batch_code/production_date/expiry_date/origin）在
   H5 契约位置 ``code_data.batch``（修复了之前的键错位 bug）。
2. 消费者解析码返回 active 检测报告 + 资质证书（在 ``code_data.test_reports`` /
   ``code_data.certificates``），来自权威 ProductAsset。
3. 后台修改产品/生产批次的权威字段后，消费者立即看到新值（缓存失效，AC1 关键证据）。
4. 缺失资产不虚构：无资产产品返回空数组而非占位。
5. 防御性 tenant 过滤：跨租户 product_id 不会串读其他租户的产品/批次/资产。

权威资料：docs/01_product/BASELINE_ACCEPTANCE_MATRIX.md §6（证据标准）、§9（溯源门禁）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_with_bypass
from app.main import app

# 验收测试：需要真实 infra PG；默认不在普通 pytest 运行中执行
pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _resolve_json(client: AsyncClient, public_id: str) -> dict:
    """以消费者身份 GET /c/{publicId}（Accept: json），返回业务契约 dict。"""
    r = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
    assert r.status_code == 200, f"resolve failed: {r.status_code} {r.text[:200]}"
    return r.json()


@pytest.fixture
async def client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    """针对 testcontainer PG 的 ASGI client（无认证，公共解析路径）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(migrated_pg_url)

    async def override_get_db():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
            await session.commit()

    async def override_get_bypass():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_bypass
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


class TestAuthoritativeBatchContract:
    """AC：消费者页展示产地/批次/生产时间 — 契约位置正确。"""

    async def test_consumer_sees_authoritative_batch_fields(self, client, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        body = await _resolve_json(client, public_id)

        # H5 契约位置：code_data.batch（之前 bug 是只在顶层 batch）
        batch = body.get("code_data", {}).get("batch")
        assert batch is not None, "code_data.batch missing — H5 cannot render traceability"
        assert batch["batch_code"] == "PB-BASE-001"
        assert batch["production_date"]  # 非空
        assert batch["expiry_date"]  # 非空
        assert batch["origin"]  # 非空

    async def test_consumer_sees_authoritative_assets(self, client, migrated_pg_url):
        """AC：消费者页展示检测报告与资质的真实关联数据。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        body = await _resolve_json(client, summary["first_public_id"])

        reports = body.get("code_data", {}).get("test_reports", [])
        certs = body.get("code_data", {}).get("certificates", [])
        assert any(r["name"] == "REPORT-BASE-001" for r in reports), (
            f"REPORT-BASE-001 missing from test_reports: {reports}"
        )
        assert any(c["name"] == "CERT-BASE-001" for c in certs), f"CERT-BASE-001 missing from certificates: {certs}"
        # 公开字段存在；不含内部字段
        report = next(r for r in reports if r["name"] == "REPORT-BASE-001")
        for field in ("name", "issuer", "file_url", "image_url"):
            assert field in report, f"report missing public field {field}"
        assert "tenant_id" not in report, "tenant_id must NOT leak to consumer"


class TestEditPropagatesImmediately:
    """AC1 关键证据：后台修改权威字段后消费者立即看到新值（缓存失效）。"""

    async def test_edit_product_origin_propagates(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        product_id = summary["product"]["id"]

        # 先 resolve 一次填满缓存
        before = await _resolve_json(client, public_id)
        assert before["code_data"]["product"]["origin"] == "基准产地"

        # 用 bypass 会话直接改权威 origin（模拟后台 PATCH /products/{id}）
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE products SET origin = '新权威产地-编辑后' WHERE id = :pid"),
            {"pid": product_id},
        )
        # 失效缓存（模拟 services.product.update_product 的调用 —— 证明该路径有效）
        from app.services.resolver_response import invalidate_product_cache

        await invalidate_product_cache(product_id)
        await bypass_session.commit()

        # 立即 re-resolve：应看到新值（若缓存未失效会看到旧值，最多 10 分钟）
        after = await _resolve_json(client, public_id)
        assert after["code_data"]["product"]["origin"] == "新权威产地-编辑后", (
            f"edit did not propagate (still {after['code_data']['product']['origin']}); cache invalidation broken"
        )

    async def test_edit_production_batch_propagates(self, client, bypass_session, migrated_pg_url):
        """编辑生产批次的 production_date 后消费者立即看到新值。"""
        from datetime import date, timedelta

        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        pb_id = summary["production_batch"]["id"]
        product_id = summary["product"]["id"]
        new_date = (date.today() - timedelta(days=30)).isoformat()  # 用于断言

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE production_batches SET production_date = :d WHERE id = :pid"),
            {"d": date.today() - timedelta(days=30), "pid": pb_id},
        )
        from app.services.resolver_response import invalidate_product_cache

        await invalidate_product_cache(product_id)  # 批次变更 → 失效其 product 缓存
        await bypass_session.commit()

        body = await _resolve_json(client, public_id)
        assert body["code_data"]["batch"]["production_date"].startswith(new_date), (
            f"batch edit did not propagate: {body['code_data']['batch']['production_date']}"
        )


class TestNoFabrication:
    """AC：缺失/未发布资料不被虚构。"""

    async def test_missing_assets_return_empty_not_placeholder(self, client, bypass_session, migrated_pg_url):
        """无资产产品 resolve 时 test_reports/certificates 为空数组（不虚构、不占位）。"""
        from app.services.public_id import generate_public_id
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        brand_id = summary["brand"]["id"]
        sku_id = summary["sku"]["id"]

        # 新建一个无资产的产品 + 单码（直接 SQL，复用 baseline 的 brand + sku 以满足 FK）
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        new_product_id = str(uuid.uuid4())
        new_pb_id = str(uuid.uuid4())
        new_cb_id = str(uuid.uuid4())
        new_item_id = str(uuid.uuid4())
        new_public_id = generate_public_id()  # 必须带 Luhn 校验位
        await bypass_session.execute(
            text(
                "INSERT INTO products (id, tenant_id, brand_id, name, status) "
                "VALUES (:id, :t, :b, '无资产产品', 'active')"
            ),
            {"id": new_product_id, "t": tenant_id, "b": brand_id},
        )
        await bypass_session.execute(
            text(
                "INSERT INTO production_batches (id, tenant_id, product_id, sku_id, batch_code, "
                "production_date, expiry_date, status) "
                "VALUES (:id, :t, :p, :s, 'PB-NOASSET-001', '2026-01-01', '2027-01-01', 'active')"
            ),
            {"id": new_pb_id, "t": tenant_id, "p": new_product_id, "s": sku_id},
        )
        await bypass_session.execute(
            text(
                "INSERT INTO code_batches (id, tenant_id, product_id, sku_id, batch_code, quantity, "
                "production_batch_id, status, code_type, generation_mode, created_by) "
                "VALUES (:id, :t, :p, :s, 'CB-NOASSET-001', 1, :pb, 'activated', 'single', 'item_level', :cb)"
            ),
            {
                "id": new_cb_id,
                "t": tenant_id,
                "p": new_product_id,
                "s": sku_id,
                "pb": new_pb_id,
                "cb": summary["baseline_tenant"]["id"],
            },
        )
        await bypass_session.execute(
            text(
                "INSERT INTO code_items (id, tenant_id, code_batch_id, public_id, status, "
                "code_type, activated_at) VALUES (:id, :t, :cb, :pid, 'activated', 'single', now())"
            ),
            {"id": new_item_id, "t": tenant_id, "cb": new_cb_id, "pid": new_public_id},
        )
        await bypass_session.commit()

        body = await _resolve_json(client, new_public_id)
        assert body["code_data"]["test_reports"] == [], (
            f"missing assets must be empty list, got {body['code_data']['test_reports']}"
        )
        assert body["code_data"]["certificates"] == []

        # 清理本测试创建的行，避免污染同会话其他验收测试（同一 acceptance DB 共享）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(text("DELETE FROM code_items WHERE id = :id"), {"id": new_item_id})
        await bypass_session.execute(text("DELETE FROM code_batches WHERE id = :id"), {"id": new_cb_id})
        await bypass_session.execute(text("DELETE FROM production_batches WHERE id = :id"), {"id": new_pb_id})
        await bypass_session.execute(text("DELETE FROM products WHERE id = :id"), {"id": new_product_id})
        await bypass_session.commit()


class TestCrossTenantDefense:
    """AC：防御性 tenant 过滤 — 不串用其他租户数据。"""

    async def test_cross_tenant_product_lookup_returns_nothing(self, bypass_session, migrated_pg_url):
        """直接验证 build_json_response 的 tenant 过滤：构造跨租户上下文不串读。

        用基准租户的 tenant_id 但传入对照租户的 product_id —— 应查不到（不抛错、不泄露）。
        """
        from app.services.resolver_response import build_json_response
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        base_tenant_id = summary["baseline_tenant"]["id"]
        # 取对照租户的产品 id（同名 BRAND-BASE/PRODUCT-BASE 但不同 tenant）
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text(
                    "SELECT p.id::text, p.tenant_id::text FROM products p "
                    "JOIN tenants t ON t.id=p.tenant_id "
                    "WHERE t.slug='baseline-control' AND p.name='PRODUCT-BASE'"
                )
            )
        ).first()
        assert row is not None, "control tenant PRODUCT-BASE must exist"
        control_product_id, control_tenant_id = row
        assert control_tenant_id != base_tenant_id

        # 构造一个伪造的 data dict：tenant_id=baseline，product_id=control（跨租户）
        fake_data = {
            "tenant_id": base_tenant_id,
            "public_id": "fake-never-exists",
            "status": "activated",
            "code_type": "single",
            "product_id": control_product_id,
            "template_id": None,
            "production_batch_id": None,
        }
        result = await build_json_response(bypass_session, fake_data, "scan-token", {"is_first_scan": False})
        # 关键断言：跨租户 product_id 不应泄露对照租户的 product 数据
        assert "product" not in result.get("code_data", {}), (
            "cross-tenant product leaked — defensive tenant filter missing"
        )
        assert result["code_data"]["test_reports"] == []
        assert result["code_data"]["certificates"] == []
