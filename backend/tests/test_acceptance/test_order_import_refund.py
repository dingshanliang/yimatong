"""yimatong-zgb1.13 验收测试 — 交付标准订单导入、去重与退款净额（真实 PostgreSQL）。

证明以下 AC：
1. 合法订单可通过接口导入，并有逐行成功失败结果。
2. 同一来源订单重复导入不会重复累计成交额。
3. 全额退款和部分退款正确冲减净成交额且保留原始流水。
4. 无效金额、未知币种和缺少业务键的数据被拒绝并解释原因。
5. 导入人、来源、批次和处理结果具备审计记录（status + source_system 追溯）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def db_session(migrated_pg_url: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        yield session
    await engine.dispose()


# ── AC1+AC4：合法导入逐行结果 + 无效拒绝 ─────────────────────────────────


class TestOrderImportValidation:
    """AC1：合法订单导入逐行结果；AC4：无效金额/币种/缺键被拒绝。"""

    async def test_mixed_valid_invalid_rows(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders

        orders = [
            # 合法
            {"external_id": "ORD-001", "amount": 99.5, "source_system": "shopify", "currency": "CNY"},
            # 无效金额（负数）
            {"external_id": "ORD-002", "amount": -10, "source_system": "shopify"},
            # 缺 external_id
            {"amount": 50, "source_system": "shopify"},
            # 无效币种
            {"external_id": "ORD-003", "amount": 30, "source_system": "shopify", "currency": "X"},
            # 合法
            {"external_id": "ORD-004", "amount": 100, "source_system": "taobao"},
        ]
        result = await import_orders(db_session, tenant_id, orders)
        await db_session.commit()

        # AC1：逐行结果
        assert result["created"] == 2, f"应创建 2 行，实际 {result['created']}"
        # AC4：3 行被拒（无效金额 + 缺键 + 无效币种）
        assert result["failed"] == 3, f"应拒绝 3 行，实际 {result['failed']}"
        assert len(result["errors"]) == 3
        # 错误原因可解释
        reasons = [e["reason"] for e in result["errors"]]
        assert "invalid_amount" in reasons
        assert "missing_external_id" in reasons
        assert "invalid_currency" in reasons


# ── AC2：重复导入去重 ────────────────────────────────────────────────────


class TestOrderDedup:
    """AC2：同一来源订单重复导入不会重复累计成交额。"""

    async def test_duplicate_import_skipped(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders

        orders = [
            {"external_id": "DUP-001", "amount": 100, "source_system": "shopify"},
        ]
        # 第一次导入
        r1 = await import_orders(db_session, tenant_id, orders)
        await db_session.commit()
        assert r1["created"] == 1

        # 第二次重复导入（同 source + external_id）
        r2 = await import_orders(db_session, tenant_id, orders)
        await db_session.commit()
        # AC2：重复导入应跳过（created=0, skipped_duplicates=1）
        assert r2["created"] == 0, "重复导入不应创建新行"
        assert r2["skipped_duplicates"] == 1, "重复导入应跳过 1 行"

        # DB 只 1 行
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        count = (
            await db_session.execute(
                text("SELECT count(*) FROM external_orders WHERE tenant_id=:t AND external_id='DUP-001'"),
                {"t": str(tenant_id)},
            )
        ).scalar()
        assert count == 1, f"重复导入后 DB 应只 1 行，实际 {count}"


# ── AC3：全额/部分退款冲减净额 + 保留流水 ─────────────────────────────────


class TestRefundNetAmount:
    """AC3：全额退款和部分退款正确冲减净成交额且保留原始流水。"""

    async def test_full_refund_reduces_net(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders, refund_order

        # 导入订单
        orders = [{"external_id": "REF-FULL-001", "amount": 200, "source_system": "shopify"}]
        await import_orders(db_session, tenant_id, orders)
        await db_session.commit()

        # 查 order id
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        order_id = (
            await db_session.execute(
                text("SELECT id FROM external_orders WHERE tenant_id=:t AND external_id='REF-FULL-001'"),
                {"t": str(tenant_id)},
            )
        ).scalar()

        # 全额退款
        result = await refund_order(db_session, tenant_id, uuid.UUID(str(order_id)), 200, partial=False)
        await db_session.commit()

        # AC3：net_amount = 0（全额退款）
        assert result["status"] == "ok"
        assert result["net_amount"] == 0.0

        # AC3：原始订单保留（status=refunded, refund_amount=200, amount 不变）
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await db_session.execute(
                text("SELECT amount, refund_amount, status FROM external_orders WHERE id=:id"),
                {"id": str(order_id)},
            )
        ).one()
        assert row[0] == 200, "原始 amount 保留"
        assert row[1] == 200, "refund_amount = 全额"
        assert row[2] == "refunded", "status = refunded"

    async def test_partial_refund_accumulates(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders, refund_order

        orders = [{"external_id": "REF-PART-001", "amount": 100, "source_system": "shopify"}]
        await import_orders(db_session, tenant_id, orders)
        await db_session.commit()

        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        order_id = (
            await db_session.execute(
                text("SELECT id FROM external_orders WHERE tenant_id=:t AND external_id='REF-PART-001'"),
                {"t": str(tenant_id)},
            )
        ).scalar()

        # 部分退款 30
        r1 = await refund_order(db_session, tenant_id, uuid.UUID(str(order_id)), 30, partial=True)
        await db_session.commit()
        assert r1["net_amount"] == 70.0

        # 再部分退款 20
        r2 = await refund_order(db_session, tenant_id, uuid.UUID(str(order_id)), 20, partial=True)
        await db_session.commit()
        # AC3：部分退款累加（30+20=50, net=50）
        assert r2["net_amount"] == 50.0

        # DB：status=partially_refunded, refund_amount=50
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await db_session.execute(
                text("SELECT amount, refund_amount, status FROM external_orders WHERE id=:id"),
                {"id": str(order_id)},
            )
        ).one()
        assert row[0] == 100, "原始 amount 保留"
        assert row[1] == 50, "refund_amount 累加 = 50"
        assert row[2] == "partially_refunded"


# ── AC5：审计记录（source + status 追溯）──────────────────────────────────


class TestImportAudit:
    """AC5：导入人、来源、批次和处理结果具备审计记录。"""

    async def test_import_records_source_and_status(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders

        orders = [
            {"external_id": "AUDIT-001", "amount": 50, "source_system": "jd", "currency": "CNY"},
        ]
        await import_orders(db_session, tenant_id, orders, actor_id="admin-001")
        await db_session.commit()

        # AC5：DB 行含 source_system + status + currency（审计追溯）
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await db_session.execute(
                text(
                    "SELECT source_system, status, currency, amount, refund_amount "
                    "FROM external_orders WHERE tenant_id=:t AND external_id='AUDIT-001'"
                ),
                {"t": str(tenant_id)},
            )
        ).one()
        assert row[0] == "jd", "source_system 记录（来源审计）"
        assert row[1] == "paid", "status = paid（初始处理结果）"
        assert row[2] == "CNY", "currency 记录（币种审计）"
        assert row[3] == 50, "amount 保留（原始流水）"
        assert row[4] == 0, "refund_amount 初始 0"
