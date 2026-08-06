"""试点里程碑派生与读取服务测试（beads: yimatong-bgag.1）。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.constants.pilot import PilotMilestoneStatus, PilotMilestoneType
from app.services.pilot_milestone import (
    build_milestone_timeline,
    derive_and_persist_milestones,
    list_milestones,
)
from tests.conftest import (
    seed_pilot_launch_release,
    seed_pilot_scan,
    seed_pilot_tenant,
)


def _as_utc(dt: datetime) -> datetime:
    """SQLite 往返会把 tz-aware datetime 读回为 naive；统一转 UTC 比较。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@pytest.mark.asyncio
async def test_derive_persists_all_derivable_milestones(db):
    """4 个可派生里程碑都写入，milestone 5 不写入（事实源未捕获）。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    confirmed = datetime(2026, 7, 5, tzinfo=UTC)
    launched = datetime(2026, 7, 10, tzinfo=UTC)
    first_scan = datetime(2026, 7, 11, tzinfo=UTC)

    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    await seed_pilot_launch_release(db, tenant_id, brand_confirmed_at=confirmed, launched_at=launched)
    await seed_pilot_scan(db, tenant_id, scan_time=first_scan)

    await derive_and_persist_milestones(db, tenant_id)

    rows = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    assert PilotMilestoneType.ONBOARDING in rows
    assert PilotMilestoneType.BRAND_CONFIRMED in rows
    assert PilotMilestoneType.LAUNCHED in rows
    assert PilotMilestoneType.FIRST_VALID_SCAN in rows
    # milestone 5 事实源未捕获，不写入
    assert PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED not in rows

    assert _as_utc(rows[PilotMilestoneType.ONBOARDING].achieved_at) == onboarding
    assert _as_utc(rows[PilotMilestoneType.LAUNCHED].achieved_at) == launched
    assert _as_utc(rows[PilotMilestoneType.FIRST_VALID_SCAN].achieved_at) == first_scan
    assert rows[PilotMilestoneType.ONBOARDING].source == "tenant.created_at"


@pytest.mark.asyncio
async def test_derive_is_idempotent_and_does_not_overwrite(db):
    """二次派生不覆盖既有里程碑（只增不改）。"""
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=datetime(2026, 7, 10, tzinfo=UTC))

    await derive_and_persist_milestones(db, tenant_id)
    first = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    original_id = first[PilotMilestoneType.LAUNCHED].id

    # 手动篡改 achieved_at（模拟"已有更早的记录"），二次派生应保持不变
    first[PilotMilestoneType.LAUNCHED].achieved_at = datetime(2000, 1, 1, tzinfo=UTC)
    await db.flush()

    await derive_and_persist_milestones(db, tenant_id)
    after = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}

    # 行数不变、id 不变、篡改值保留（说明派生没有覆盖）
    assert after[PilotMilestoneType.LAUNCHED].id == original_id
    assert _as_utc(after[PilotMilestoneType.LAUNCHED].achieved_at) == datetime(2000, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_derive_idempotent_on_repeated_calls(db):
    """重复多次派生不报错、不新增重复行（幂等）。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await seed_pilot_launch_release(db, tenant_id, launched_at=datetime(2026, 7, 10, tzinfo=UTC))

    for _ in range(3):
        await derive_and_persist_milestones(db, tenant_id)

    rows = await list_milestones(db, tenant_id)
    types = [r.milestone_type for r in rows]
    assert types.count(PilotMilestoneType.LAUNCHED) == 1
    assert types.count(PilotMilestoneType.ONBOARDING) == 1


@pytest.mark.asyncio
async def test_derive_skips_missing_facts(db):
    """租户从未上线、无有效扫码：只写开通里程碑，其余不写入。"""
    tenant_id = await seed_pilot_tenant(db)
    await derive_and_persist_milestones(db, tenant_id)

    rows = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    assert PilotMilestoneType.ONBOARDING in rows
    assert PilotMilestoneType.BRAND_CONFIRMED not in rows
    assert PilotMilestoneType.LAUNCHED not in rows
    assert PilotMilestoneType.FIRST_VALID_SCAN not in rows


@pytest.mark.asyncio
async def test_first_valid_scan_ignores_invalid_visits(db):
    """is_valid_visit=False 的扫码不计入首次扫码里程碑。"""
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_scan(db, tenant_id, scan_time=datetime(2026, 6, 1, tzinfo=UTC), is_valid_visit=False)

    await derive_and_persist_milestones(db, tenant_id)

    rows = await list_milestones(db, tenant_id)
    assert not any(r.milestone_type == PilotMilestoneType.FIRST_VALID_SCAN for r in rows)


@pytest.mark.asyncio
async def test_timeline_marks_unachieved_including_m5(db):
    """时间线：未达成里程碑（含 milestone 5）标 not_achieved。"""
    tenant_id = await seed_pilot_tenant(db)
    timeline = await build_milestone_timeline(db, tenant_id)

    by_type = {m.type: m for m in timeline.milestones}
    assert len(timeline.milestones) == 5
    assert by_type["onboarding"].status == PilotMilestoneStatus.ACHIEVED.value
    # milestone 5 当前事实源未捕获发布时间，按 PRD §4.1 标"未达成"（非 §4.4 的"数据不足"）
    assert by_type["first_campaign_published"].status == PilotMilestoneStatus.NOT_ACHIEVED.value
    assert by_type["first_campaign_published"].achieved_at is None
    assert by_type["launched"].status == PilotMilestoneStatus.NOT_ACHIEVED.value
    assert by_type["launched"].achieved_at is None


@pytest.mark.asyncio
async def test_timeline_derived_durations_computed(db):
    """派生时长：开通→上线、上线→首扫。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    launched = datetime(2026, 7, 11, tzinfo=UTC)  # +10 天
    first_scan = datetime(2026, 7, 12, tzinfo=UTC)  # +1 天

    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await seed_pilot_scan(db, tenant_id, scan_time=first_scan)

    timeline = await build_milestone_timeline(db, tenant_id)
    by_label = {d.label: d for d in timeline.derived_durations}

    onboarding_to_launch = by_label["开通→上线时长"]
    assert onboarding_to_launch.status == "computed"
    assert onboarding_to_launch.seconds == timedelta(days=10).total_seconds()

    launch_to_scan = by_label["上线→首扫时长"]
    assert launch_to_scan.status == "computed"
    assert launch_to_scan.seconds == timedelta(days=1).total_seconds()


@pytest.mark.asyncio
async def test_timeline_derived_durations_insufficient_when_partial(db):
    """只有开通、没有上线：开通→上线时长为数据不足（派生指标口径，PRD §4.4）。"""
    tenant_id = await seed_pilot_tenant(db)
    timeline = await build_milestone_timeline(db, tenant_id)

    by_label = {d.label: d for d in timeline.derived_durations}
    assert by_label["开通→上线时长"].status == PilotMilestoneStatus.INSUFFICIENT_DATA.value
    assert by_label["开通→上线时长"].seconds is None


@pytest.mark.asyncio
async def test_tenant_isolation_between_tenants(db):
    """A 租户派生不影响 B 租户。"""
    a_id = await seed_pilot_tenant(db)
    b_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, b_id, launched_at=datetime(2026, 7, 10, tzinfo=UTC))

    await derive_and_persist_milestones(db, a_id)

    a_rows = await list_milestones(db, a_id)
    b_rows = await list_milestones(db, b_id)
    # A 只有开通（无上线事实）；B 尚未派生故为空
    assert {r.milestone_type for r in a_rows} == {PilotMilestoneType.ONBOARDING}
    assert b_rows == []
