"""试点里程碑 §6.2 更正机制 + 首个活动发布 published_at 捕获测试（beads: yimatong-bgag.7）。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.constants.campaign import CampaignStatus
from app.constants.pilot import PilotMilestoneStatus, PilotMilestoneType
from app.services.campaign import change_campaign_status, create_campaign
from app.services.pilot_milestone import (
    build_milestone_timeline,
    correct_milestone,
    derive_and_persist_milestones,
    list_milestones,
)
from tests.conftest import (
    seed_pilot_launch_release,
    seed_pilot_tenant,
)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ── 里程碑 5：首个活动发布 published_at 捕获 ──────────────────────────


@pytest.mark.asyncio
async def test_milestone5_derives_from_earliest_campaign_published_at(db):
    """首个活动发布里程碑：取该租户最早的 Campaign.published_at。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)

    c1 = await create_campaign(
        db, tenant_id, "c1", "discount", "2026-07-01T00:00:00Z", "2026-08-31T00:00:00Z", rules_json={}
    )
    c2 = await create_campaign(
        db, tenant_id, "c2", "discount", "2026-07-01T00:00:00Z", "2026-08-31T00:00:00Z", rules_json={}
    )
    # c2 先发布，c1 后发布 → 应取 c2 的更早 published_at
    c2_activated = await change_campaign_status(db, tenant_id, uuid.UUID(c2["id"]), CampaignStatus.ACTIVE)
    earlier_published = c2_activated["published_at"]
    await change_campaign_status(db, tenant_id, uuid.UUID(c1["id"]), CampaignStatus.ACTIVE)
    await db.flush()

    await derive_and_persist_milestones(db, tenant_id)
    rows = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    m5 = rows[PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED]
    # 取两条 published_at 中更早者（c2 先激活）
    assert _as_utc(m5.achieved_at) == _as_utc(earlier_published)
    assert m5.source == "campaigns.published_at"


@pytest.mark.asyncio
async def test_change_campaign_status_sets_published_at_once(db):
    """change_campaign_status 首次切到 ACTIVE 写 published_at；二次（PAUSED→ACTIVE）不复写。"""
    tenant_id = await seed_pilot_tenant(db)
    c = await create_campaign(
        db, tenant_id, "c1", "discount", "2026-07-01T00:00:00Z", "2026-08-31T00:00:00Z", rules_json={}
    )
    first = await change_campaign_status(db, tenant_id, uuid.UUID(c["id"]), CampaignStatus.ACTIVE)
    first_published = first["published_at"]
    assert first_published is not None

    # 暂停后再激活：published_at 不变（只记首次）
    paused = await change_campaign_status(db, tenant_id, uuid.UUID(c["id"]), CampaignStatus.PAUSED)
    assert paused["published_at"] == first_published
    reactivated = await change_campaign_status(db, tenant_id, uuid.UUID(c["id"]), CampaignStatus.ACTIVE)
    assert reactivated["published_at"] == first_published


@pytest.mark.asyncio
async def test_milestone5_absent_without_published_campaign(db):
    """活动从未发布（无 published_at）：里程碑 5 不写入，时间线标 not_achieved。"""
    tenant_id = await seed_pilot_tenant(db)
    await create_campaign(
        db, tenant_id, "draft-c", "discount", "2026-07-01T00:00:00Z", "2026-08-31T00:00:00Z", rules_json={}
    )
    timeline = await build_milestone_timeline(db, tenant_id)
    m5 = next(m for m in timeline.milestones if m.type == PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED.value)
    assert m5.status == PilotMilestoneStatus.NOT_ACHIEVED.value
    assert m5.achieved_at is None


# ── §6.2 更正记录机制 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_correct_milestone_appends_without_overwriting_original(db):
    """更正追加一条记录，不改写 PilotMilestone.achieved_at 原始事实。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await derive_and_persist_milestones(db, tenant_id)
    rows = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    original = _as_utc(rows[PilotMilestoneType.ONBOARDING].achieved_at)
    milestone_id = rows[PilotMilestoneType.ONBOARDING].id

    corrected = datetime(2026, 6, 15, tzinfo=UTC)
    correction = await correct_milestone(
        db,
        tenant_id,
        PilotMilestoneType.ONBOARDING,
        corrected,
        reason="原始开通时间录入有误，回填真实签约日",
        source="manual_correction",
    )
    assert correction is not None
    await db.flush()

    # 原始里程碑行 achieved_at 不变
    after = {r.milestone_type: r for r in await list_milestones(db, tenant_id)}
    assert _as_utc(after[PilotMilestoneType.ONBOARDING].achieved_at) == original
    assert after[PilotMilestoneType.ONBOARDING].id == milestone_id


@pytest.mark.asyncio
async def test_timeline_shows_corrected_value_and_correction_chain(db):
    """时间线展示更正后的值，并附更正记录链（PRD §6.2）。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await derive_and_persist_milestones(db, tenant_id)

    first_corr = datetime(2026, 6, 20, tzinfo=UTC)
    second_corr = datetime(2026, 6, 15, tzinfo=UTC)
    await correct_milestone(db, tenant_id, PilotMilestoneType.ONBOARDING, first_corr, reason="第一次更正", source="s1")
    await correct_milestone(db, tenant_id, PilotMilestoneType.ONBOARDING, second_corr, reason="第二次更正", source="s2")
    await db.flush()

    timeline = await build_milestone_timeline(db, tenant_id)
    m = next(x for x in timeline.milestones if x.type == PilotMilestoneType.ONBOARDING.value)
    # 最新一条更正为有效值
    assert _as_utc(m.achieved_at) == second_corr
    assert m.source == "s2"
    assert len(m.corrections) == 2
    # 更正链按时间升序，reason 完整
    assert m.corrections[0].reason == "第一次更正"
    assert m.corrections[1].reason == "第二次更正"
    # 原始事实始终暴露以供审计（PRD §6.2）
    assert _as_utc(m.original_achieved_at) == datetime(2026, 7, 1, tzinfo=UTC)
    assert m.original_source == "tenant.created_at"


@pytest.mark.asyncio
async def test_correct_milestone_uses_corrected_values_for_derived_durations(db):
    """派生时长基于更正后的有效达成时间计算。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    launched = datetime(2026, 7, 11, tzinfo=UTC)  # 原始 +10 天
    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await derive_and_persist_milestones(db, tenant_id)

    # 更正开通时间为 6 月 25 日 → 开通→上线变成 16 天
    await correct_milestone(
        db,
        tenant_id,
        PilotMilestoneType.ONBOARDING,
        datetime(2026, 6, 25, tzinfo=UTC),
        reason="回填真实开通日",
        source="manual",
    )
    await db.flush()

    timeline = await build_milestone_timeline(db, tenant_id)
    by_label = {d.label: d for d in timeline.derived_durations}
    assert by_label["开通→上线时长"].seconds == timedelta(days=16).total_seconds()


@pytest.mark.asyncio
async def test_correct_milestone_requires_reason(db):
    """更正必须填 reason（PRD §6.2 透明口径）。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await derive_and_persist_milestones(db, tenant_id)

    with pytest.raises(ValueError):
        await correct_milestone(
            db,
            tenant_id,
            PilotMilestoneType.ONBOARDING,
            datetime(2026, 6, 15, tzinfo=UTC),
            reason="   ",
            source="manual",
        )


@pytest.mark.asyncio
async def test_correct_milestone_returns_none_for_unachieved(db):
    """更正尚未达成的里程碑：返回 None（无可更正对象）。"""
    tenant_id = await seed_pilot_tenant(db)
    result = await correct_milestone(
        db,
        tenant_id,
        PilotMilestoneType.FIRST_VALID_SCAN,
        datetime(2026, 7, 1, tzinfo=UTC),
        reason="test",
        source="manual",
    )
    assert result is None


@pytest.mark.asyncio
async def test_corrections_isolated_by_tenant(db):
    """A 租户的更正不影响 B 租户时间线。"""
    a_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    b_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await derive_and_persist_milestones(db, a_id)
    await derive_and_persist_milestones(db, b_id)

    await correct_milestone(
        db, a_id, PilotMilestoneType.ONBOARDING, datetime(2026, 6, 1, tzinfo=UTC), reason="A 更正", source="A"
    )
    await db.flush()

    b_timeline = await build_milestone_timeline(db, b_id)
    b_onboarding = next(x for x in b_timeline.milestones if x.type == PilotMilestoneType.ONBOARDING.value)
    assert len(b_onboarding.corrections) == 0
