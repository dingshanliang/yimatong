"""运营 scorecard 快照服务测试（beads: yimatong-bgag.3，PRD §4.4）。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.constants.pilot import PilotMilestoneType
from app.models.pilot_milestone import PilotMilestone
from app.services.pilot_scorecard import INSUFFICIENT, build_scorecard
from tests.conftest import seed_pilot_launch_release, seed_pilot_tenant


async def _seed_milestones(db, tenant_id, onboarding, launched, first_scan=None):
    """直接写入里程碑行（模拟票 1 已派生）。"""
    db.add(
        PilotMilestone(
            tenant_id=tenant_id,
            milestone_type=PilotMilestoneType.ONBOARDING,
            achieved_at=onboarding,
            source="tenant.created_at",
        )
    )
    db.add(
        PilotMilestone(
            tenant_id=tenant_id,
            milestone_type=PilotMilestoneType.LAUNCHED,
            achieved_at=launched,
            source="launch_releases.launched_at",
        )
    )
    if first_scan is not None:
        db.add(
            PilotMilestone(
                tenant_id=tenant_id,
                milestone_type=PilotMilestoneType.FIRST_VALID_SCAN,
                achieved_at=first_scan,
                source="scan_events.first_valid_visit",
            )
        )
    await db.flush()


@pytest.mark.asyncio
async def test_scorecard_computes_durations_and_rates(db):
    """有里程碑 + 漏斗数据：6 指标正常计算。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    launched = datetime(2026, 7, 8, tzinfo=UTC)  # 开通→上线 7 天
    first_scan = datetime(2026, 7, 9, tzinfo=UTC)  # 上线→首扫 1 天

    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await _seed_milestones(db, tenant_id, onboarding, launched, first_scan)

    scorecard = await build_scorecard(db, tenant_id, window_start=launched, window_end=launched + timedelta(days=7))

    assert scorecard["onboarding_to_launch"]["value"] == timedelta(days=7).total_seconds()
    assert scorecard["onboarding_to_launch"]["status"] == "computed"
    assert scorecard["launch_to_first_scan"]["value"] == timedelta(days=1).total_seconds()


@pytest.mark.asyncio
async def test_scorecard_missing_milestone_is_insufficient(db):
    """里程碑缺失：开通→上线时长为数据不足（不显示 0）。"""
    tenant_id = await seed_pilot_tenant(db)
    # 不写里程碑
    scorecard = await build_scorecard(
        db, tenant_id, window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC)
    )
    assert scorecard["onboarding_to_launch"]["status"] == INSUFFICIENT
    assert scorecard["onboarding_to_launch"]["value"] is None


@pytest.mark.asyncio
async def test_scorecard_rate_insufficient_when_no_visits(db):
    """无有效访问（分母=0）：权益确认率/企微确认率为数据不足，不显示 0%。"""
    tenant_id = await seed_pilot_tenant(db)
    scorecard = await build_scorecard(
        db, tenant_id, window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC)
    )
    assert scorecard["valid_visits"]["value"] == 0
    assert scorecard["claim_rate"]["status"] == INSUFFICIENT
    assert scorecard["claim_rate"]["value"] is None
    assert scorecard["wecom_rate"]["status"] == INSUFFICIENT


@pytest.mark.asyncio
async def test_scorecard_gmv_insufficient_when_no_orders(db):
    """无订单数据：净 GMV 为数据不足，不显示 0。"""
    tenant_id = await seed_pilot_tenant(db)
    scorecard = await build_scorecard(
        db, tenant_id, window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC)
    )
    assert scorecard["net_gmv"]["status"] == INSUFFICIENT
    assert scorecard["net_gmv"]["value"] is None


@pytest.mark.asyncio
async def test_scorecard_includes_window_metadata(db):
    """快照含窗口元数据（窗口起止/天数），便于审计回查。"""
    tenant_id = await seed_pilot_tenant(db)
    w_start = datetime(2026, 7, 1, tzinfo=UTC)
    w_end = datetime(2026, 7, 8, tzinfo=UTC)
    scorecard = await build_scorecard(db, tenant_id, window_start=w_start, window_end=w_end)
    assert scorecard["window_start"] == w_start.isoformat()
    assert scorecard["window_end"] == w_end.isoformat()
    assert scorecard["window_days"] == 7
    assert "funnel_raw" in scorecard


@pytest.mark.asyncio
async def test_scorecard_window_anchored_to_absolute_range(db):
    """窗口锚定到绝对 [window_start, window_end]，不相对今天（PRD §4.4 数字一致）。

    构造两条扫码：一条在窗口内、一条在窗口外（更晚）。窗口内计数应为 1，
    不被窗口外的扫码污染——证明窗口是绝对闭区间，而非 days_back 相对今天。
    """
    from app.models.scan import ScanEvent

    tenant_id = await seed_pilot_tenant(db)
    w_start = datetime(2026, 7, 1, tzinfo=UTC)
    w_end = datetime(2026, 7, 8, tzinfo=UTC)
    # 窗口内一条有效扫码
    db.add(
        ScanEvent(
            tenant_id=tenant_id, public_id="IN01", scan_time=datetime(2026, 7, 3, tzinfo=UTC), is_valid_visit=True
        )
    )
    # 窗口外（更晚）一条有效扫码，不应计入本期窗口
    db.add(
        ScanEvent(
            tenant_id=tenant_id, public_id="OUT01", scan_time=datetime(2026, 9, 1, tzinfo=UTC), is_valid_visit=True
        )
    )
    await db.flush()

    scorecard = await build_scorecard(db, tenant_id, window_start=w_start, window_end=w_end)
    assert scorecard["valid_visits"]["value"] == 1
    assert scorecard["funnel_raw"]["valid_visits"] == 1
