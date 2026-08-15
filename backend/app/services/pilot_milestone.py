"""试点里程碑派生与读取服务（beads: yimatong-bgag.1 / bgag.7）。

PRD docs/prd/pilot-learning-retrospective.md §4.1 + §6.2。

里程碑是事实记录，只由系统事件首次发生写入，只增不改。
派生函数幂等：同租户同里程碑已存在则不覆盖；并发首写由唯一约束
uq_pilot_milestones_tenant_type 兜底（begin_nested + IntegrityError 回退为 no-op）。

事实源映射：
- 客户开通      → Tenant.created_at
- 品牌确认      → LaunchRelease.brand_confirmed_at（取该租户最早的已确认值）
- 正式上线      → LaunchRelease.launched_at（取该租户最早的已上线值）
- 首次扫码      → scan_events 中 is_valid_visit=true 的最早 scan_time
- 首个活动发布  → Campaign.published_at（change_campaign_status 首次切到 ACTIVE 时写入，
                  beads: yimatong-bgag.7；取该租户最早的 published_at）

更正机制（PRD §6.2）：correct_milestone 追加一条 PilotMilestoneCorrection，
不改写 PilotMilestone.achieved_at 原始事实；读取层在存在更正时以最新一条更正后的
achieved_at 作为展示值并附更正链。
"""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.constants.pilot import (
    PILOT_MILESTONE_LABELS,
    PILOT_MILESTONE_ORDER,
    PilotMilestoneStatus,
    PilotMilestoneType,
)
from app.core.database import _session_uses_postgresql
from app.models.campaign import Campaign
from app.models.launch import LaunchRelease
from app.models.pilot_milestone import PilotMilestone, PilotMilestoneCorrection
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.schemas.pilot_milestone import (
    DerivedDuration,
    MilestoneCorrectionItem,
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

    # 5. 首个活动发布：最早的活动 published_at（beads: yimatong-bgag.7）
    first_published_at = (
        await db.execute(
            select(Campaign.published_at)
            .where(
                Campaign.tenant_id == tenant_id,
                Campaign.published_at.is_not(None),
            )
            .order_by(Campaign.published_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if first_published_at is not None:
        facts.append((PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED, first_published_at, "campaigns.published_at"))

    return facts


async def list_milestones(db: AsyncSession, tenant_id: uuid.UUID) -> list[PilotMilestone]:
    """读取已持久化的里程碑（按达成时间排序，便于时间线展示）。"""
    result = await db.execute(
        select(PilotMilestone).where(PilotMilestone.tenant_id == tenant_id).order_by(PilotMilestone.achieved_at.asc())
    )
    return list(result.scalars().all())


async def build_milestone_timeline(
    db: AsyncSession, tenant_id: uuid.UUID, *, auth_session_id: uuid.UUID | None = None
) -> MilestoneTimelineResponse:
    """组装里程碑时间线响应：懒派生 + 5 个里程碑 + 派生时长 + 更正记录。"""
    if _session_uses_postgresql(db):
        if auth_session_id is None:
            raise ValueError("A live auth session is required to materialize pilot milestones")
        requested_ids = {milestone.value: str(uuid7()) for milestone in PILOT_MILESTONE_ORDER}
        await db.execute(
            text(
                "SELECT * FROM public.materialize_pilot_milestones("
                ":tenant_id,:auth_session_id,CAST(:requested_ids AS jsonb))"
            ),
            {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "requested_ids": json.dumps(requested_ids, separators=(",", ":")),
            },
        )
    else:
        await derive_and_persist_milestones(db, tenant_id)

    rows = await list_milestones(db, tenant_id)
    by_type: dict[PilotMilestoneType, PilotMilestone] = {r.milestone_type: r for r in rows}

    # 更正记录：按 milestone_id 聚合，每里程碑取最新一条作为展示 achieved_at（PRD §6.2）。
    corrections_by_milestone = await _load_corrections_by_milestone(db, tenant_id)
    # 更正后的展示值（若有更正，用最新 corrected_at 替换）
    effective_achieved_at: dict[PilotMilestoneType, datetime] = {
        mtype: row.achieved_at for mtype, row in by_type.items() if row is not None
    }
    for row in by_type.values():
        corrs = corrections_by_milestone.get(row.id, [])
        if corrs:
            # 最新一条（按 created_at 升序加载，最后一条为最新）
            effective_achieved_at[row.milestone_type] = corrs[-1].corrected_at

    items: list[MilestoneItem] = []
    for mtype in PILOT_MILESTONE_ORDER:
        row = by_type.get(mtype)
        if row is not None:
            corrs = corrections_by_milestone.get(row.id, [])
            items.append(
                MilestoneItem(
                    type=mtype.value,
                    label=PILOT_MILESTONE_LABELS[mtype],
                    status=PilotMilestoneStatus.ACHIEVED.value,
                    # 展示值：有更正取最新更正，否则原始事实（PRD §6.2）
                    achieved_at=effective_achieved_at[mtype],
                    source=(corrs[-1].source if corrs else row.source),
                    # 原始事实始终暴露以供审计（PRD §6.2：原始记录只增不改）
                    original_achieved_at=row.achieved_at,
                    original_source=row.source,
                    corrections=[_correction_to_item(c) for c in corrs],
                )
            )
        else:
            # 未达成：事件尚未发生，或事实源尚未捕获
            items.append(
                MilestoneItem(
                    type=mtype.value,
                    label=PILOT_MILESTONE_LABELS[mtype],
                    status=PilotMilestoneStatus.NOT_ACHIEVED.value,
                    achieved_at=None,
                    source=None,
                )
            )

    durations = _compute_derived_durations(effective_achieved_at)

    return MilestoneTimelineResponse(
        tenant_id=tenant_id,
        milestones=items,
        derived_durations=durations,
    )


async def _load_corrections_by_milestone(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[uuid.UUID, list[PilotMilestoneCorrection]]:
    """加载本租户所有里程碑的更正记录，按 (milestone_id → 升序 created_at 列表) 聚合。"""
    result = await db.execute(
        select(PilotMilestoneCorrection)
        .where(PilotMilestoneCorrection.tenant_id == tenant_id)
        .order_by(PilotMilestoneCorrection.milestone_id, PilotMilestoneCorrection.created_at.asc())
    )
    grouped: dict[uuid.UUID, list[PilotMilestoneCorrection]] = {}
    for corr in result.scalars().all():
        grouped.setdefault(corr.milestone_id, []).append(corr)
    return grouped


def _correction_to_item(corr: PilotMilestoneCorrection) -> MilestoneCorrectionItem:
    return MilestoneCorrectionItem(
        corrected_at=corr.corrected_at,
        source=corr.source,
        reason=corr.reason,
        corrected_by=corr.corrected_by,
        created_at=corr.created_at,
    )


async def correct_milestone(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    milestone_type: PilotMilestoneType,
    corrected_at: datetime,
    *,
    reason: str,
    source: str,
    corrected_by: uuid.UUID | None = None,
    platform_auth_session_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    payload_digest: str | None = None,
) -> PilotMilestoneCorrection | None:
    """追加一条里程碑更正记录（PRD §6.2）。

    不改写 PilotMilestone.achieved_at 原始事实；只追加更正行。读取层在展示时
    以最新更正为有效值。reason 必填。里程碑不存在则返回 None（无可更正对象）。
    """
    if not reason or not reason.strip():
        raise ValueError("更正记录必须填写原因（PRD §6.2）")
    if _session_uses_postgresql(db):
        if None in (platform_auth_session_id, request_id, idempotency_key, payload_digest):
            raise ValueError("Platform session and idempotency evidence are required")
        result = await db.execute(
            text(
                "SELECT correction_id FROM public.append_pilot_milestone_correction("
                ":tenant_id,:platform_session_id,:request_id,:milestone_type,:corrected_at,"
                ":source,:reason,:idempotency_key,:payload_digest)"
            ),
            {
                "tenant_id": tenant_id,
                "platform_session_id": platform_auth_session_id,
                "request_id": request_id,
                "milestone_type": milestone_type.value,
                "corrected_at": corrected_at,
                "source": source,
                "reason": reason,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        correction_id = result.scalar_one()
        return (
            await db.execute(
                select(PilotMilestoneCorrection).where(
                    PilotMilestoneCorrection.tenant_id == tenant_id,
                    PilotMilestoneCorrection.id == correction_id,
                )
            )
        ).scalar_one()
    row = (
        await db.execute(
            select(PilotMilestone.id).where(
                PilotMilestone.tenant_id == tenant_id,
                PilotMilestone.milestone_type == milestone_type,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    correction = PilotMilestoneCorrection(
        tenant_id=tenant_id,
        milestone_id=row,
        milestone_type=milestone_type,
        corrected_at=corrected_at,
        source=source,
        reason=reason.strip(),
        corrected_by=corrected_by,
    )
    db.add(correction)
    await db.flush()
    return correction


def _compute_derived_durations(
    effective_at: dict[PilotMilestoneType, datetime],
) -> list[DerivedDuration]:
    """计算派生时长（PRD §4.1：开通→上线、上线→首扫）。

    用更正后的有效达成时间计算（PRD §6.2），缺失源数据时为 None 并标记"数据不足"
    （PRD §4.4 scorecard 口径仅用于派生指标）。
    """

    def _build(label: str, frm: PilotMilestoneType, to: PilotMilestoneType) -> DerivedDuration:
        f = effective_at.get(frm)
        t = effective_at.get(to)
        if f is None or t is None:
            return DerivedDuration(
                label=label,
                from_type=frm.value,
                to_type=to.value,
                seconds=None,
                status=PilotMilestoneStatus.INSUFFICIENT_DATA.value,
            )
        delta = (t - f).total_seconds()
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
