"""试点里程碑派生与读取服务（beads: yimatong-bgag.1）。

PRD docs/prd/pilot-learning-retrospective.md §4.1 + §6.2。

里程碑是事实记录，只由系统事件首次发生写入，只增不改。
派生函数幂等：同租户同里程碑已存在则不覆盖；并发首写由唯一约束
uq_pilot_milestones_tenant_type 兜底（begin_nested + IntegrityError 回退为 no-op）。

事实源映射：
- 客户开通      → Tenant.created_at
- 品牌确认      → LaunchRelease.brand_confirmed_at（取该租户最早的已确认值）
- 正式上线      → LaunchRelease.launched_at（取该租户最早的已上线值）
- 首次扫码      → scan_events 中 is_valid_visit=true 的最早 scan_time
- 首个活动发布  → 当前事实源尚未捕获发布时间戳（change_campaign_status 仅发内存信号、
                  不落审计，Campaign 无 published_at 列），本版不写入、读取层标记"未达成"。
                  事实源捕获见后续票（beads follow-up）。
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.pilot import (
    PILOT_MILESTONE_LABELS,
    PILOT_MILESTONE_ORDER,
    PilotMilestoneStatus,
    PilotMilestoneType,
)
from app.models.launch import LaunchRelease
from app.models.pilot_milestone import PilotMilestone
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.schemas.pilot_milestone import (
    DerivedDuration,
    MilestoneItem,
    MilestoneTimelineResponse,
)

logger = logging.getLogger(__name__)


async def _upsert_milestone(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    milestone_type: PilotMilestoneType,
    achieved_at: datetime,
    source: str,
) -> None:
    """幂等写入一个里程碑；已存在则不覆盖（只增不改），并发首写安全。

    先 SELECT 快速跳过已存在行；INSERT 放进 savepoint，唯一约束冲突时回滚
    savepoint（即并发对手已先写入）并继续，保证调用幂等且不抛 IntegrityError。
    """
    existing = await db.execute(
        select(PilotMilestone.id)
        .where(
            PilotMilestone.tenant_id == tenant_id,
            PilotMilestone.milestone_type == milestone_type,
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return
    try:
        async with db.begin_nested():
            db.add(
                PilotMilestone(
                    tenant_id=tenant_id,
                    milestone_type=milestone_type,
                    achieved_at=achieved_at,
                    source=source,
                )
            )
            await db.flush()
    except IntegrityError:
        # 并发对手已写入该里程碑（uq_pilot_milestones_tenant_type 兜底），等价于 no-op
        return


async def derive_and_persist_milestones(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """派生并持久化可从现有事实源得到的里程碑（幂等）。

    不抛错：某个事实源缺失时跳过对应里程碑（不写入），由读取层标记"未达成"。
    """
    facts = await _gather_derived_facts(db, tenant_id)
    for milestone_type, achieved_at, source in facts:
        await _upsert_milestone(db, tenant_id, milestone_type, achieved_at, source)


async def _gather_derived_facts(
    db: AsyncSession, tenant_id: uuid.UUID
) -> list[tuple[PilotMilestoneType, datetime, str]]:
    """从各事实源收集可派生的里程碑 (类型, 达成时间, 来源说明)。缺失的事实源跳过。"""

    facts: list[tuple[PilotMilestoneType, datetime, str]] = []

    # 1. 客户开通：租户创建时间
    tenant_row = await db.get(Tenant, tenant_id)
    if tenant_row is not None and tenant_row.created_at is not None:
        facts.append((PilotMilestoneType.ONBOARDING, tenant_row.created_at, "tenant.created_at"))

    # 2/3. 品牌确认 / 正式上线：取该租户最早的已确认 / 已上线值
    brand_confirmed_at = (
        await db.execute(
            select(LaunchRelease.brand_confirmed_at)
            .where(
                LaunchRelease.tenant_id == tenant_id,
                LaunchRelease.brand_confirmed_at.is_not(None),
            )
            .order_by(LaunchRelease.brand_confirmed_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if brand_confirmed_at is not None:
        facts.append((PilotMilestoneType.BRAND_CONFIRMED, brand_confirmed_at, "launch_releases.brand_confirmed_at"))

    launched_at = (
        await db.execute(
            select(LaunchRelease.launched_at)
            .where(
                LaunchRelease.tenant_id == tenant_id,
                LaunchRelease.launched_at.is_not(None),
            )
            .order_by(LaunchRelease.launched_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if launched_at is not None:
        facts.append((PilotMilestoneType.LAUNCHED, launched_at, "launch_releases.launched_at"))

    # 4. 首次扫码：最早的有效扫码 scan_time
    first_scan_at = (
        await db.execute(
            select(ScanEvent.scan_time)
            .where(
                ScanEvent.tenant_id == tenant_id,
                ScanEvent.is_valid_visit.is_(True),
            )
            .order_by(ScanEvent.scan_time.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if first_scan_at is not None:
        facts.append((PilotMilestoneType.FIRST_VALID_SCAN, first_scan_at, "scan_events.first_valid_visit"))

    # 5. 首个活动发布：事实源尚未捕获发布时间戳（见模块 docstring），本版不写入。

    return facts


async def list_milestones(db: AsyncSession, tenant_id: uuid.UUID) -> list[PilotMilestone]:
    """读取已持久化的里程碑（按达成时间排序，便于时间线展示）。"""
    result = await db.execute(
        select(PilotMilestone).where(PilotMilestone.tenant_id == tenant_id).order_by(PilotMilestone.achieved_at.asc())
    )
    return list(result.scalars().all())


async def build_milestone_timeline(db: AsyncSession, tenant_id: uuid.UUID) -> MilestoneTimelineResponse:
    """组装里程碑时间线响应：懒派生 + 5 个里程碑 + 派生时长。"""
    await derive_and_persist_milestones(db, tenant_id)

    rows = await list_milestones(db, tenant_id)
    by_type: dict[PilotMilestoneType, PilotMilestone] = {r.milestone_type: r for r in rows}

    items: list[MilestoneItem] = []
    for mtype in PILOT_MILESTONE_ORDER:
        row = by_type.get(mtype)
        if row is not None:
            items.append(
                MilestoneItem(
                    type=mtype.value,
                    label=PILOT_MILESTONE_LABELS[mtype],
                    status=PilotMilestoneStatus.ACHIEVED.value,
                    achieved_at=row.achieved_at,
                    source=row.source,
                )
            )
        else:
            # 未达成：事件尚未发生，或事实源尚未捕获（首个活动发布见模块 docstring）
            items.append(
                MilestoneItem(
                    type=mtype.value,
                    label=PILOT_MILESTONE_LABELS[mtype],
                    status=PilotMilestoneStatus.NOT_ACHIEVED.value,
                    achieved_at=None,
                    source=None,
                )
            )

    durations = _compute_derived_durations(by_type)

    return MilestoneTimelineResponse(
        tenant_id=tenant_id,
        milestones=items,
        derived_durations=durations,
    )


def _compute_derived_durations(
    by_type: dict[PilotMilestoneType, PilotMilestone],
) -> list[DerivedDuration]:
    """计算派生时长（PRD §4.1：开通→上线、上线→首扫）。

    缺失源数据时为 None 并标记"数据不足"（PRD §4.4 scorecard 口径仅用于派生指标）。
    """

    def _build(label: str, frm: PilotMilestoneType, to: PilotMilestoneType) -> DerivedDuration:
        f = by_type.get(frm)
        t = by_type.get(to)
        if f is None or t is None or f.achieved_at is None or t.achieved_at is None:
            return DerivedDuration(
                label=label,
                from_type=frm.value,
                to_type=to.value,
                seconds=None,
                status=PilotMilestoneStatus.INSUFFICIENT_DATA.value,
            )
        delta = (t.achieved_at - f.achieved_at).total_seconds()
        return DerivedDuration(
            label=label,
            from_type=frm.value,
            to_type=to.value,
            seconds=delta,
            status="computed",
        )

    return [
        _build("开通→上线时长", PilotMilestoneType.ONBOARDING, PilotMilestoneType.LAUNCHED),
        _build("上线→首扫时长", PilotMilestoneType.LAUNCHED, PilotMilestoneType.FIRST_VALID_SCAN),
    ]
