"""§8 上期动作显式承接 + §4.4 GMV 归因窗口测试（beads: yimatong-bgag.8）。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.constants.pilot import PilotMilestoneType
from app.models.gmv import GmvAttribution
from app.models.pilot_milestone import PilotMilestone
from app.services.pilot_scorecard import build_scorecard
from app.services.retrospective import (
    complete_retrospective,
    generate_for_tenant,
    list_retrospectives,
)
from tests.conftest import seed_pilot_launch_release, seed_pilot_tenant


def _seed_attr(tenant_id, *, amount, scan_time, order_id=None, public_id="SCAN-ATTR"):
    """构造一条 GmvAttribution（归因快照，amount 已为净额）。"""
    return GmvAttribution(
        tenant_id=tenant_id,
        external_order_id=order_id or uuid.uuid4(),
        public_id=public_id,
        amount=amount,
        match_type="phone",
        scan_time=scan_time,
        attribution_window_hours=168,
        confidence_score=0.95,
    )


# ── §4.4 GMV 归因快照口径 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gmv_uses_attribution_snapshot_by_scan_time(db):
    """§4.4 row 6：净 GMV 来源 = 归因快照，按 scan_time 在本期窗口聚合。

    归因快照每行的 attribution_window_hours 已在写入时固化扫码→下单延迟，
    故本期窗口 [window_start, window_end] 内的扫码归因自然包含延迟下单。
    """
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    launched = datetime(2026, 7, 8, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    db.add(
        PilotMilestone(
            tenant_id=tenant_id, milestone_type=PilotMilestoneType.ONBOARDING, achieved_at=onboarding, source="t"
        )
    )
    db.add(
        PilotMilestone(
            tenant_id=tenant_id, milestone_type=PilotMilestoneType.LAUNCHED, achieved_at=launched, source="l"
        )
    )
    await db.flush()

    window_start = launched
    window_end = launched + timedelta(days=7)
    # 本期窗口内的扫码归因（即便下单发生在归因窗口内，已通过 scan_time 纳入）
    db.add(_seed_attr(tenant_id, amount=100.0, scan_time=window_start + timedelta(days=2), public_id="A1"))
    await db.flush()

    scorecard = await build_scorecard(db, tenant_id, window_start=window_start, window_end=window_end)
    assert scorecard["net_gmv"]["value"] == 100.0
    assert scorecard["net_gmv"]["status"] == "computed"
    assert scorecard["gmv_source"] == "gmv_attributions"
    assert scorecard["funnel_raw"]["attributed_orders"] == 1


@pytest.mark.asyncio
async def test_gmv_excludes_attributions_outside_scan_window(db):
    """scan_time 不在本期窗口内的归因不计入 GMV。"""
    tenant_id = await seed_pilot_tenant(db)
    window_start = datetime(2026, 7, 1, tzinfo=UTC)
    window_end = datetime(2026, 7, 8, tzinfo=UTC)
    # scan_time 在窗口之前
    db.add(_seed_attr(tenant_id, amount=500.0, scan_time=window_start - timedelta(days=1), public_id="OUT1"))
    await db.flush()

    scorecard = await build_scorecard(db, tenant_id, window_start=window_start, window_end=window_end)
    assert scorecard["net_gmv"]["status"] == "insufficient_data"


@pytest.mark.asyncio
async def test_gmv_amount_is_net_after_refund(db):
    """归因快照 amount 已是退款回冲后净额，scorecard 直接采用。"""
    tenant_id = await seed_pilot_tenant(db)
    window_start = datetime(2026, 7, 1, tzinfo=UTC)
    window_end = datetime(2026, 7, 8, tzinfo=UTC)
    # 归因快照净额 = 150（已扣除退款，由 gmv.refund_order 在写入时固化）
    db.add(_seed_attr(tenant_id, amount=150.0, scan_time=window_start + timedelta(days=1), public_id="NET1"))
    await db.flush()

    scorecard = await build_scorecard(db, tenant_id, window_start=window_start, window_end=window_end)
    assert scorecard["net_gmv"]["value"] == 150.0
    assert scorecard["funnel_raw"]["attributed_net_amount"] == 150.0


# ── §8 上期动作显式承接 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_period_carries_over_uncompleted_previous_actions(db):
    """§8：新一期复盘承接上一期未完成动作（carryover=True）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    # 生成第 7 天复盘
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro7 = (await list_retrospectives(db, tenant_id))[0]
    # 第 7 天填两个动作：一个完成、一个未完成
    await complete_retrospective(
        db,
        tenant_id,
        retro7.id,
        actor_id=uuid.uuid4(),
        actions=[
            {"content": "已完成动作", "owner_id": None, "due_date": None, "status": "completed"},
            {"content": "未完成动作", "owner_id": None, "due_date": None, "status": "pending"},
        ],
    )
    await db.flush()

    # 生成第 14 天复盘：应承接第 7 天的未完成动作
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=20))
    retros = await list_retrospectives(db, tenant_id)
    retro14 = next(r for r in retros if r.period_day == 14)
    carried = [a for a in retro14.actions if a.get("carryover") is True]
    assert len(carried) == 1
    assert carried[0]["content"] == "未完成动作"
    assert carried[0]["carryover_disposition"] is None
    assert carried[0]["status"] == "pending"
    # 已完成的动作不承接
    assert not any(a.get("content") == "已完成动作" for a in retro14.actions)


@pytest.mark.asyncio
async def test_complete_blocked_when_carryover_actions_undisposed(db):
    """§8：存在未处置的承接动作时，完成本期复盘被阻断。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro7 = (await list_retrospectives(db, tenant_id))[0]
    await complete_retrospective(
        db,
        tenant_id,
        retro7.id,
        actor_id=uuid.uuid4(),
        actions=[{"content": "遗留动作", "owner_id": None, "due_date": None, "status": "pending"}],
    )
    await db.flush()

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=20))
    retro14 = next(r for r in await list_retrospectives(db, tenant_id) if r.period_day == 14)
    # 不处置直接尝试完成 → 阻断
    with pytest.raises(ValueError, match="未显式处置"):
        await complete_retrospective(db, tenant_id, retro14.id, actor_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_complete_succeeds_when_carryover_actions_disposed(db):
    """§8：所有承接动作显式处置后，完成本期复盘放行。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro7 = (await list_retrospectives(db, tenant_id))[0]
    await complete_retrospective(
        db,
        tenant_id,
        retro7.id,
        actor_id=uuid.uuid4(),
        actions=[{"content": "遗留动作", "owner_id": None, "due_date": None, "status": "pending"}],
    )
    await db.flush()

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=20))
    retro14 = next(r for r in await list_retrospectives(db, tenant_id) if r.period_day == 14)
    # 显式处置承接动作
    disposed = [
        {
            **a,
            "carryover_disposition": "abandon",
        }
        for a in retro14.actions
    ]
    result = await complete_retrospective(db, tenant_id, retro14.id, actor_id=uuid.uuid4(), actions=disposed)
    assert result is not None
    assert result.status.value == "completed"


@pytest.mark.asyncio
async def test_first_period_has_no_carryover(db):
    """首期（第 7 天）复盘无上一期，actions 为空（无承接）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro7 = (await list_retrospectives(db, tenant_id))[0]
    assert retro7.actions == []


@pytest.mark.asyncio
async def test_carryover_cannot_be_silently_dropped_via_actions_overwrite(db):
    """§8：调用方用新 actions 列表丢掉未处置的承接动作时，服务层重新挂回并阻断。

    不允许承接动作静默消失（PRD §8）。
    """
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro7 = (await list_retrospectives(db, tenant_id))[0]
    await complete_retrospective(
        db,
        tenant_id,
        retro7.id,
        actor_id=uuid.uuid4(),
        actions=[{"content": "遗留动作", "owner_id": None, "due_date": None, "status": "pending"}],
    )
    await db.flush()
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=20))
    retro14 = next(r for r in await list_retrospectives(db, tenant_id) if r.period_day == 14)

    # 试图用一个全新（不含承接动作）的 actions 列表完成 → 仍被阻断
    with pytest.raises(ValueError, match="未显式处置"):
        await complete_retrospective(
            db,
            tenant_id,
            retro14.id,
            actor_id=uuid.uuid4(),
            actions=[{"content": "全新动作", "owner_id": None, "due_date": None, "status": "pending"}],
        )
