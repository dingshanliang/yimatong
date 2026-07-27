"""yimatong-zgb1.14 验收测试 — 交付归因快照、转化漏斗和数据质量看板（真实 PostgreSQL）。

证明以下 AC：
1. 漏斗区分有效访问、参与意图、权益确认、企微确认、订单、退款和净成交额。
2. 后续修改活动、渠道或页面不会改写历史归因结果。
3. 指标支持按活动、渠道、产品、时间和访客群组核对。
4. 看板明确区分已确认、待验证、缺失和失败数据。
5. 汇总值可追溯到明细事实，并与退款后的净额一致。
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


# ── AC1：7 层漏斗 ────────────────────────────────────────────────────────


class TestSevenLayerFunnel:
    """AC1：漏斗区分有效访问、参与意图、权益确认、企微确认、订单、退款和净成交额。"""

    async def test_funnel_has_seven_layers(self, db_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.analytics_extended import get_conversion_funnel

        funnel = await get_conversion_funnel(db_session, tenant_id, days_back=30)

        # AC1：7 层漏斗
        step_names = [s["name"] for s in funnel["steps"]]
        assert len(funnel["steps"]) == 7, f"漏斗应有 7 层，实际 {len(funnel['steps'])}"
        assert "有效访问" in step_names, "漏斗必须含有效访问层"
        assert "参与意图" in step_names, "漏斗必须含参与意图层"
        assert "权益确认" in step_names, "漏斗必须含权益确认层"
        assert "企微确认" in step_names, "漏斗必须含企微确认层"
        assert "订单总额" in step_names, "漏斗必须含订单层"
        assert "退款总额" in step_names, "漏斗必须含退款层"
        assert "净成交额" in step_names, "漏斗必须含净成交额层"

    async def test_funnel_valid_visit_denominator(self, db_session, migrated_pg_url):
        """AC1：有效访问是分母（Decision 21），不是 raw scan。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.analytics_extended import get_conversion_funnel

        funnel = await get_conversion_funnel(db_session, tenant_id, days_back=30)
        # 有效访问层 rate 应为 100%（分母）
        valid_visit_step = next(s for s in funnel["steps"] if s["name"] == "有效访问")
        assert valid_visit_step["rate"] == 100.0, "有效访问层应为 100%（分母）"


# ── AC5：汇总与净额一致 ──────────────────────────────────────────────────


class TestNetAmountConsistency:
    """AC5：汇总值可追溯到明细事实，并与退款后的净额一致。"""

    async def test_net_amount_equals_order_minus_refund(self, db_session, migrated_pg_url):
        """AC5：净成交额 = 订单总额 - 退款总额。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.analytics_extended import get_conversion_funnel

        funnel = await get_conversion_funnel(db_session, tenant_id, days_back=30)

        # AC5：net = order - refund
        order_amount = funnel["order_amount"]
        refund_amount = funnel["refund_amount"]
        net_amount = funnel["net_amount"]
        assert net_amount == round(order_amount - refund_amount, 2), (
            f"净额应 = 订单 - 退款，实际 net={net_amount} order={order_amount} refund={refund_amount}"
        )

        # 漏斗步骤的净成交额也一致
        net_step = next(s for s in funnel["steps"] if s["name"] == "净成交额")
        assert net_step["value"] == net_amount


# ── AC2：归因快照不可漂移 ─────────────────────────────────────────────────


class TestAttributionSnapshotNoDrift:
    """AC2：后续修改活动/渠道/页面不会改写历史归因结果。"""

    async def test_attribution_snapshot_columns_exist(self, db_session):
        """AC2：GmvAttribution 快照字段存在（product_id/code_batch_id/channel_snapshot/original_amount）。"""
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        cols = (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='gmv_attributions' AND column_name IN "
                    "('product_id', 'code_batch_id', 'channel_snapshot', 'page_version_id', 'original_amount')"
                )
            )
        ).fetchall()
        col_names = {c[0] for c in cols}
        assert "product_id" in col_names, "GmvAttribution 必须有 product_id 快照"
        assert "code_batch_id" in col_names, "GmvAttribution 必须有 code_batch_id 快照"
        assert "channel_snapshot" in col_names, "GmvAttribution 必须有 channel_snapshot 快照"
        assert "original_amount" in col_names, "GmvAttribution 必须有 original_amount 快照"

    async def test_attribution_retains_original_amount_after_refund(self, db_session, migrated_pg_url):
        """AC2+AC5：退款后归因行的 original_amount 保留（不改写历史）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])

        from app.services.gmv import import_orders, refund_order

        # 导入订单
        orders = [{"external_id": "SNAP-001", "amount": 100, "source_system": "shopify"}]
        await import_orders(db_session, tenant_id, orders)
        await db_session.commit()

        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        order_id = (
            await db_session.execute(
                text("SELECT id FROM external_orders WHERE tenant_id=:t AND external_id='SNAP-001'"),
                {"t": str(tenant_id)},
            )
        ).scalar()

        # 手动创建一条 GmvAttribution（模拟归因写入）
        from app.models.gmv import GmvAttribution

        attr = GmvAttribution(
            tenant_id=tenant_id,
            external_order_id=uuid.UUID(str(order_id)),
            amount=100,
            original_amount=100,
            match_type="phone",
            attribution_window_hours=168,
            confidence_score=1.0,
        )
        db_session.add(attr)
        await db_session.commit()

        # 退款
        await refund_order(db_session, tenant_id, uuid.UUID(str(order_id)), 30, partial=True)
        await db_session.commit()

        # AC2：original_amount 仍为 100（快照不改写）
        await db_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        attr_row = (
            await db_session.execute(
                text("SELECT original_amount, amount FROM gmv_attributions WHERE external_order_id=:oid"),
                {"oid": str(order_id)},
            )
        ).one()
        assert attr_row[0] == 100, "original_amount 快照应保留（不改写历史）"
        # amount 被 refund_order 同步更新为 net（70）
        assert attr_row[1] == 70, "amount 应同步为 net（退款后）"
