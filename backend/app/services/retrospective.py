"""试点复盘生成与状态机服务（beads: yimatong-bgag.2，PRD pilot-learning-retrospective §4.2/§4.3/§6.1/§7）。

- poller 跨租户幂等生成到期复盘（7/14/30 天）。
- 状态机：pending → completed；overdue 为计算态（不落库）。
- 完成后快照冻结，只允许追加 supplementary_notes。
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.constants.retrospective import (
    RETO_STATE_OVERDUE,
    RETO_STATE_OVERDUE_COMPLETED,
    RETRO_PERIOD_DAYS,
    RetrospectiveStatus,
)
from app.core.database import _session_uses_postgresql
from app.models.launch import LaunchRelease
from app.models.retrospective import Retrospective
from app.models.tenant import (
    AgencyAuthorization,
    AgencyAuthStatus,
    OpsTask,
    OpsTaskPriority,
    OpsTaskStatus,
)
from app.schemas.retrospective import ACTION_DISPOSITIONS
from app.services.pilot_scorecard import build_scorecard

logger = logging.getLogger(__name__)


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite 往返会把 tz-aware datetime 读回为 naive；统一补 UTC 再做比较/算术。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _first_launched_at(db: AsyncSession, tenant_id: uuid.UUID) -> datetime | None:
    """取该租户最早的 launched_at（首次正式上线）。"""
    result = await db.execute(
        select(LaunchRelease.launched_at)
        .where(
            LaunchRelease.tenant_id == tenant_id,
            LaunchRelease.launched_at.is_not(None),
        )
        .order_by(LaunchRelease.launched_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _due_periods(launched_at: datetime, now: datetime) -> list[tuple[int, datetime, datetime, date]]:
    """计算已到期的期次：(period_day, window_start, window_end, next_review_date)。

    window = [launched_at, launched_at + period_day]；到期即 now >= window_end。
    next_review_date 默认下一期复盘日（或最后一期后 +30 天）。
    """
    due: list[tuple[int, datetime, datetime, date]] = []
    for i, period in enumerate(RETRO_PERIOD_DAYS):
        window_end = launched_at + timedelta(days=period)
        if now < window_end:
            continue
        # 下一期复盘日
        next_idx = i + 1
        if next_idx < len(RETRO_PERIOD_DAYS):
            next_review = (launched_at + timedelta(days=RETRO_PERIOD_DAYS[next_idx])).date()
        else:
            # 最后一期（30 天）后默认 +30 天
            next_review = (window_end + timedelta(days=30)).date()
        due.append((period, launched_at, window_end, next_review))
    return due


async def _generate_one(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_day: int,
    window_start: datetime,
    window_end: datetime,
    next_review_date: date,
) -> bool:
    """幂等生成单期复盘。已存在则 no-op。返回是否新建。

    新一期复盘会承接上一期未完成的动作（PRD §8：上期动作未完成 → 新一期必须
    显式选择继续/调整/放弃，不允许静默消失）。承接的动作以 carryover=True 标记，
    carryover_disposition 留空，完成本期复盘前必须显式处置。
    """
    existing = await db.execute(
        select(Retrospective.id).where(
            Retrospective.tenant_id == tenant_id,
            Retrospective.period_day == period_day,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False

    scorecard = await build_scorecard(db, tenant_id, window_start, window_end)
    # 上期动作承接（PRD §8）：取上一期（period_day 更小的最近一期）未完成动作
    carried_actions = await _carryover_actions_from_previous(db, tenant_id, period_day)
    # 提醒负责人（PRD §4.2：路由到该租户的代运营 owner）
    owner_id = await _resolve_agency_owner(db, tenant_id)

    if _session_uses_postgresql(db):
        from app.core.database import control_session_factory

        scorecard_json = json.dumps(scorecard, separators=(",", ":"), default=str)
        retrospective_id, ops_task_id = uuid7(), uuid7()
        for attempt in range(3):
            try:
                async with control_session_factory() as control_db:
                    await control_db.execute(text("SELECT set_config('app.tenant_id','',true)"))
                    await control_db.execute(text("SELECT set_config('app.bypass_rls','true',true)"))
                    snapshot_digest = await control_db.scalar(
                        text(
                            "SELECT encode(digest(convert_to(CAST(:scorecard AS jsonb)::text,'UTF8'),'sha256'),'hex')"
                        ),
                        {"scorecard": scorecard_json},
                    )
                    created = await control_db.scalar(
                        text(
                            "SELECT created FROM public.materialize_due_retrospective("
                            ":tenant_id,:retrospective_id,:ops_task_id,:period_day,:window_start,:window_end,"
                            ":next_review_date,CAST(:scorecard AS jsonb),:snapshot_digest,:assigned_to)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "retrospective_id": retrospective_id,
                            "ops_task_id": ops_task_id,
                            "period_day": period_day,
                            "window_start": window_start,
                            "window_end": window_end,
                            "next_review_date": next_review_date,
                            "scorecard": scorecard_json,
                            "snapshot_digest": snapshot_digest,
                            "assigned_to": owner_id,
                        },
                    )
                    await control_db.commit()
                return bool(created)
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                if sqlstate != "55P03" or attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))
        raise RuntimeError("bounded retrospective generation retry exhausted")

    try:
        async with db.begin_nested():
            retro = Retrospective(
                tenant_id=tenant_id,
                period_day=period_day,
                window_start=window_start,
                window_end=window_end,
                next_review_date=next_review_date,
                status=RetrospectiveStatus.PENDING,
                scorecard_snapshot=scorecard,
                actions=carried_actions,
            )
            db.add(retro)
            await db.flush()
            # 同事务创建关联 OpsTask 提醒入口（PRD §4.2/§6.3）：
            # OpsTask 仅作工作台待办提醒，不承载复盘数据。
            task = _build_reminder_ops_task(tenant_id, period_day, next_review_date, assigned_to=owner_id)
            db.add(task)
            await db.flush()
            retro.ops_task_id = task.id
            await db.flush()
    except IntegrityError:
        # 并发对手已生成同期复盘，等价于 no-op
        return False
    return True


async def _resolve_agency_owner(db: AsyncSession, client_tenant_id: uuid.UUID) -> uuid.UUID | None:
    """解析该客户租户的代运营负责人（PRD §4.2/§5：OpsTask 提醒负责人路由）。

    PRD §5：代运营人员（agency 侧）生成提醒、填写复盘。AgencyAuthorization.granted_by
    是授权该代运营的品牌方账号（_require_brand，见 agency_auth.create_authorization），
    不能作为负责人。本函数取该客户最近一条 active 授权的 agency_tenant_id，再从该
    代运营租户内找一个 active 的运营负责人（有 campaign:manage 权限的 admin/operator 账号，
    对齐复盘 PATCH 的权限要求），优先最近登录者；无匹配时返回 None（任务进入待认领池）。
    """
    # 1. 取该客户最近一条 active 代运营授权的 agency_tenant_id
    agency_tenant_id = (
        await db.execute(
            select(AgencyAuthorization.agency_tenant_id)
            .where(
                AgencyAuthorization.client_tenant_id == client_tenant_id,
                AgencyAuthorization.status == AgencyAuthStatus.active,
            )
            .order_by(AgencyAuthorization.granted_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if agency_tenant_id is None:
        return None

    # 2. 在该代运营租户内找运营负责人（active 账号 + campaign:manage 权限，对齐复盘填写权限）
    from app.models.tenant import Account, Permission, Role, account_roles, role_permissions

    owner_id = (
        await db.execute(
            select(Account.id)
            .join(account_roles, account_roles.c.account_id == Account.id)
            .join(Role, Role.id == account_roles.c.role_id)
            .join(role_permissions, role_permissions.c.role_id == Role.id)
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(
                Account.tenant_id == agency_tenant_id,
                Account.is_active.is_(True),
                Permission.code == "campaign:manage",
            )
            .order_by(Account.last_login_at.desc().nulls_last(), Account.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return owner_id


async def _carryover_actions_from_previous(db: AsyncSession, tenant_id: uuid.UUID, period_day: int) -> list[dict]:
    """读取上一期复盘的未完成动作，构造为新一期承接动作（PRD §8）。

    未完成 = status != "completed"。每个承接动作复制内容/owner/due_date，置
    carryover=True、carryover_disposition=None、status="pending"，等待新一期显式处置。
    无上一期或上期无未完成动作时返回 []。
    """
    prev = (
        await db.execute(
            select(Retrospective)
            .where(
                Retrospective.tenant_id == tenant_id,
                Retrospective.period_day < period_day,
            )
            .order_by(Retrospective.period_day.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if prev is None:
        return []
    carried: list[dict] = []
    for action in prev.actions or []:
        if not isinstance(action, dict):
            continue
        if action.get("status") == "completed":
            continue
        carried.append(
            {
                "content": action.get("content", ""),
                "owner_id": action.get("owner_id"),
                "due_date": action.get("due_date"),
                "status": "pending",
                "carryover": True,
                "carryover_disposition": None,
            }
        )
    return carried


def _build_reminder_ops_task(
    tenant_id: uuid.UUID, period_day: int, next_review_date: date, *, assigned_to: uuid.UUID | None = None
) -> OpsTask:
    """构造复盘提醒 OpsTask（镜像 ops.py 字段集）。

    assigned_to 路由到该租户的代运营 owner（PRD §4.2，由 _resolve_agency_owner 解析）；
    无明确 owner 时为 None（待代运营认领）。due_date 对齐 next_review_date（PRD §6.1 逾期口径）。
    """
    return OpsTask(
        tenant_id=tenant_id,
        title=f"试点复盘第{period_day}天待填写",
        description=f"请完成上线后第 {period_day} 天的试点复盘（里程碑/漏斗数据已预填）。",
        priority=OpsTaskPriority.medium,
        due_date=datetime.combine(next_review_date, datetime.min.time(), tzinfo=UTC),
        assigned_to=assigned_to,
    )


async def generate_for_tenant(db: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """为单租户生成所有到期复盘。返回新生成数量。"""
    now = now or datetime.now(UTC)
    launched_at = await _first_launched_at(db, tenant_id)
    if launched_at is None:
        # 从未上线不生成（PRD §4.2 Failure）
        return 0
    launched_at = _ensure_aware(launched_at)

    created = 0
    for period, w_start, w_end, next_review in _due_periods(launched_at, now):
        if await _generate_one(db, tenant_id, period, w_start, w_end, next_review):
            created += 1
    return created


async def generate_due_retrospectives() -> int:
    """Poller 入口：跨租户扫描已上线租户，生成到期复盘。

    使用 bootstrap_tenant_keys（control session + bypass）取 (tenant_id,) 对，
    每租户开 fresh session + set_session_tenant_context 后调用 generate_for_tenant。
    """
    from app.core.database import (
        async_session_factory,
        bootstrap_tenant_keys,
        set_session_tenant_context,
    )
    from app.models.tenant import Tenant, TenantStatus

    # 取所有 active 租户 id（跨租户 control session）。
    # bootstrap_tenant_keys 要求 2 列 (object_id, tenant_id)；对 tenants 表二者同为 id。
    async with async_session_factory() as bootstrap_db:
        work_keys = await bootstrap_tenant_keys(
            bootstrap_db,
            select(Tenant.id, Tenant.id).where(Tenant.status == TenantStatus.active).order_by(Tenant.id).limit(1000),
        )
    tenant_ids = [tid for _, tid in work_keys]

    total = 0
    for tenant_id in tenant_ids:
        try:
            async with async_session_factory() as db:
                await set_session_tenant_context(db, tenant_id)
                # 仅处理已上线租户（generate_for_tenant 内部判断）
                total += await generate_for_tenant(db, tenant_id)
                await db.commit()
        except Exception:
            logger.exception("Failed to generate retrospectives for tenant %s", tenant_id)
    logger.info("Retrospective generation completed: %s created", total)
    return total


async def complete_retrospective(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    retro_id: uuid.UUID,
    *,
    issues: str | None = None,
    actions: list[dict] | None = None,
    next_review_date: date | None = None,
    goal: str | None = None,
    actor_id: uuid.UUID | None = None,
    supplementary_notes: str | None = None,
) -> Retrospective | None:
    """状态机 pending → completed。

    完成后快照冻结：scorecard_snapshot 不可改。允许写 issues/actions/goal/
    next_review_date/supplementary_notes。
    """
    result = await db.execute(
        select(Retrospective).where(
            Retrospective.tenant_id == tenant_id,
            Retrospective.id == retro_id,
        )
    )
    retro = result.scalar_one_or_none()
    if retro is None:
        return None
    if retro.status == RetrospectiveStatus.COMPLETED:
        # 已完成：只允许追加 supplementary_notes（PRD §6.1）
        if supplementary_notes is not None:
            existing = retro.supplementary_notes or ""
            joined = existing + "\n" + supplementary_notes if existing else supplementary_notes
            retro.supplementary_notes = joined.strip()
            await db.flush()
        return retro

    if issues is not None:
        retro.issues = issues
    if actions is not None:
        # PRD §8：不允许承接动作静默消失。调用方若试图用新 actions 列表丢掉尚未处置的
        # 承接动作，则把这些未处置的承接动作重新挂回，强制其显式处置（含 abandon）。
        retro.actions = _merge_carryover_on_overwrite(retro.actions or [], actions)
    if next_review_date is not None:
        retro.next_review_date = next_review_date
    if goal is not None:
        retro.goal = goal
    # PRD §8：上期承接动作必须显式处置（继续/调整/放弃），不允许静默消失。
    _enforce_carryover_disposition(retro)
    retro.status = RetrospectiveStatus.COMPLETED
    retro.completed_at = datetime.now(UTC)
    retro.completed_by = actor_id
    if supplementary_notes is not None:
        retro.supplementary_notes = supplementary_notes
    # 提醒闭环：关联 OpsTask 同步置 completed（仅当非终态，PRD §4.2）
    await _complete_linked_ops_task(db, retro.ops_task_id, retro.tenant_id, actor_id)
    await db.flush()
    return retro


async def _complete_linked_ops_task(
    db: AsyncSession,
    ops_task_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """把关联 OpsTask 置 completed（若存在且非终态）并留审计。

    complete_retrospective 由人工触发（有 actor_id），故 OpsTask 状态变更需审计
    （与 ops.py 的 ops_task_updated 审计对齐）。
    """
    if ops_task_id is None:
        return
    task = await db.get(OpsTask, ops_task_id)
    if task is None or task.status in (OpsTaskStatus.completed, OpsTaskStatus.cancelled):
        return
    before = task.status
    task.status = OpsTaskStatus.completed
    # 审计：人工完成复盘时同步关闭提醒任务
    if actor_id is not None:
        from app.services.audit import write_audit_log

        await write_audit_log(
            db,
            str(actor_id),
            str(tenant_id),
            "ops_task_updated",
            f"ops_task:{task.id}",
            {"before": {"status": before.value}, "after": {"status": "completed"}, "source": "retrospective_complete"},
        )


def _enforce_carryover_disposition(retro: Retrospective) -> None:
    """PRD §8：上期承接动作（carryover=True）必须在完成本期复盘前显式处置。

    未处置（carryover_disposition 为空）的承接动作阻断完成，抛 ValueError。
    合法处置值：continue/adjust/abandon（见 schemas.retrospective.ACTION_DISPOSITIONS）。
    """
    pending = []
    for action in retro.actions or []:
        if isinstance(action, dict) and action.get("carryover") is True:
            disposition = action.get("carryover_disposition")
            if disposition not in ACTION_DISPOSITIONS:
                pending.append(action.get("content", "(无内容)"))
    if pending:
        raise ValueError("存在未显式处置的上期承接动作，必须逐项选择继续/调整/放弃：\n- " + "\n- ".join(pending))


def _merge_carryover_on_overwrite(existing: list, incoming: list) -> list:
    """PRD §8：覆盖 actions 时，未处置的承接动作必须保留，不允许静默丢弃。

    以 (content, owner_id, due_date) 为业务键匹配：incoming 中若未出现某条未处置
    承接动作（carryover=True 且 carryover_disposition 为空），则把它重新挂回 incoming，
    强制调用方必须显式处置（含 abandon）才能完成复盘。
    """
    if not isinstance(incoming, list):
        return existing

    def _key(a: dict) -> tuple:
        return (a.get("content"), str(a.get("owner_id") or ""), str(a.get("due_date") or ""))

    incoming_keys = {_key(a) for a in incoming if isinstance(a, dict) and a.get("carryover") is True}
    preserved: list = []
    for a in existing:
        if not isinstance(a, dict) or a.get("carryover") is not True:
            continue
        if a.get("carryover_disposition") in ACTION_DISPOSITIONS:
            continue  # 已处置，允许 incoming 覆盖/丢弃
        if _key(a) not in incoming_keys:
            preserved.append(a)
    return [*incoming, *preserved]


async def update_retrospective(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    retro_id: uuid.UUID,
    *,
    goal: str | None = None,
    issues: str | None = None,
    actions: list[dict] | None = None,
    next_review_date: date | None = None,
    supplementary_notes: str | None = None,
    mark_completed: bool = False,
    actor_id: uuid.UUID | None = None,
    auth_session_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
    payload_digest: str | None = None,
) -> Retrospective | None:
    """更新复盘字段（状态感知，PRD §6.1）。

    - mark_completed=True：走 complete_retrospective（pending→completed，快照冻结）。
    - 已完成：仅允许追加 supplementary_notes（委托 complete_retrospective 的完成分支）。
    - pending 编辑：可改 goal/issues/actions/next_review_date（不推进状态）。
      用 begin_nested + 状态重检防止并发完成导致越过冻结边界。
    """
    if _session_uses_postgresql(db):
        if None in (auth_session_id, request_id, expected_version, idempotency_key, payload_digest):
            raise ValueError("Live session, version, and idempotency evidence are required")
        current = (
            await db.execute(
                select(Retrospective).where(
                    Retrospective.tenant_id == tenant_id,
                    Retrospective.id == retro_id,
                )
            )
        ).scalar_one_or_none()
        if current is None:
            return None
        if current.status == RetrospectiveStatus.COMPLETED:
            if supplementary_notes is None:
                return current
            signature = "append_retrospective_note_authority"
            sql = (
                f"SELECT retrospective_id FROM public.{signature}("
                ":tenant_id,:auth_session_id,:retro_id,:request_id,:expected_version,:note,"
                ":idempotency_key,:payload_digest)"
            )
            params = {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "retro_id": retro_id,
                "request_id": request_id,
                "expected_version": expected_version,
                "note": supplementary_notes,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            }
        else:
            signature = (
                "complete_retrospective_authority" if mark_completed else "update_pending_retrospective_authority"
            )
            note_argument = ",:supplementary_note" if mark_completed else ""
            sql = (
                f"SELECT retrospective_id FROM public.{signature}("
                ":tenant_id,:auth_session_id,:retro_id,:request_id,:expected_version,:goal,:issues,"
                f"CAST(:actions AS jsonb),:next_review_date{note_argument},:idempotency_key,:payload_digest)"
            )
            params = {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "retro_id": retro_id,
                "request_id": request_id,
                "expected_version": expected_version,
                "goal": goal,
                "issues": issues,
                "actions": json.dumps(actions, separators=(",", ":")) if actions is not None else None,
                "next_review_date": next_review_date,
                "supplementary_note": supplementary_notes,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            }
        await db.execute(text(sql), params)
        db.expire(current)
        return (
            await db.execute(
                select(Retrospective)
                .where(Retrospective.tenant_id == tenant_id, Retrospective.id == retro_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    if mark_completed:
        return await complete_retrospective(
            db,
            tenant_id,
            retro_id,
            issues=issues,
            actions=actions,
            next_review_date=next_review_date,
            goal=goal,
            actor_id=actor_id,
            supplementary_notes=supplementary_notes,
        )

    result = await db.execute(
        select(Retrospective).where(
            Retrospective.tenant_id == tenant_id,
            Retrospective.id == retro_id,
        )
    )
    retro = result.scalar_one_or_none()
    if retro is None:
        return None

    if retro.status == RetrospectiveStatus.COMPLETED:
        # 已完成：只允许追加 supplementary_notes
        if supplementary_notes is not None:
            return await complete_retrospective(db, tenant_id, retro_id, supplementary_notes=supplementary_notes)
        return retro

    # pending 编辑：begin_nested 内改字段，重检状态防并发完成越界
    try:
        async with db.begin_nested():
            if goal is not None:
                retro.goal = goal
            if issues is not None:
                retro.issues = issues
            if actions is not None:
                retro.actions = actions
            if next_review_date is not None:
                retro.next_review_date = next_review_date
            await db.flush()
            # 重检：并发对手若刚完成，则放弃本次编辑
            await db.refresh(retro, attribute_names=["status"])
            if retro.status == RetrospectiveStatus.COMPLETED:
                raise IntegrityError("concurrent completion", params=None, orig=None)
    except IntegrityError:
        # 并发完成：编辑回退，按完成态只追加 supplementary_notes
        if supplementary_notes is not None:
            return await complete_retrospective(db, tenant_id, retro_id, supplementary_notes=supplementary_notes)
    return retro


def _derived_status(retro: Retrospective, now: datetime) -> str:
    """根据 now vs next_review_date 派生展示状态（不落库）。"""
    if retro.status == RetrospectiveStatus.COMPLETED:
        # 完成时间晚于 next_review_date → 逾期完成
        if retro.completed_at is not None and retro.completed_at.date() > retro.next_review_date:
            return RETO_STATE_OVERDUE_COMPLETED
        return RetrospectiveStatus.COMPLETED.value
    # pending：超过 next_review_date → 逾期
    if now.date() > retro.next_review_date:
        return RETO_STATE_OVERDUE
    return RetrospectiveStatus.PENDING.value


async def list_retrospectives(
    db: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None
) -> list[Retrospective]:
    """读取本租户所有复盘（按期次升序）。"""
    now = now or datetime.now(UTC)
    result = await db.execute(
        select(Retrospective).where(Retrospective.tenant_id == tenant_id).order_by(Retrospective.period_day.asc())
    )
    return list(result.scalars().all())


async def get_retrospective(db: AsyncSession, tenant_id: uuid.UUID, retro_id: uuid.UUID) -> Retrospective | None:
    result = await db.execute(
        select(Retrospective).where(
            Retrospective.tenant_id == tenant_id,
            Retrospective.id == retro_id,
        )
    )
    return result.scalar_one_or_none()
