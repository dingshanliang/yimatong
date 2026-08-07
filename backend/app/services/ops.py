"""代运营工作台与上线检查服务"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignStatus
from app.models.code import CodeBatch
from app.models.page import PageVersion, PageVersionStatus
from app.models.product import Brand, Product
from app.models.tenant import OpsTask, OpsTaskPriority, OpsTaskStatus, Tenant, TenantStatus
from app.services.agency_auth import get_authorized_client_scopes
from app.utils import escape_like_pattern

READINESS_STEPS = [
    ("brand_configured", "配置品牌", "/brands"),
    ("product_created", "创建产品", "/products"),
    ("page_published", "发布扫码页", "/pages"),
    ("code_batch_activated", "激活码批次", "/codes"),
]


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_task_overdue(task: OpsTask, now: datetime) -> bool:
    if task.status not in (OpsTaskStatus.pending, OpsTaskStatus.in_progress) or not task.due_date:
        return False
    return _as_aware_utc(task.due_date) < now


def build_readiness_summary(status: dict) -> dict:
    derived = {
        "brand_configured": status.get("brands", 0) >= 1,
        "product_created": status.get("products", 0) >= 1,
        "page_published": status.get("published_pages", 0) >= 1,
        "code_batch_activated": status.get("activated_batches", 0) >= 1,
    }
    missing = [(key, label, href) for key, label, href in READINESS_STEPS if not derived.get(key)]
    total_count = len(READINESS_STEPS)
    passed_count = total_count - len(missing)
    return {
        "ready": passed_count == total_count,
        "passed_count": passed_count,
        "total_count": total_count,
        "percent": round((passed_count / total_count) * 100) if total_count else 0,
        "missing_keys": [key for key, _, _ in missing],
        "missing_labels": [label for _, label, _ in missing],
    }


def _empty_workbench(page: int, page_size: int) -> dict:
    return {
        "summary": {
            "total_clients": 0,
            "active_clients": 0,
            "ready_clients": 0,
            "blocked_clients": 0,
            "pending_tasks": 0,
            "in_progress_tasks": 0,
            "overdue_tasks": 0,
        },
        "clients": [],
        "tasks": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


def build_next_action(tenant_name: str, readiness: dict, task_summary: dict) -> dict:
    if task_summary["overdue"] > 0:
        return {
            "type": "overdue_task",
            "label": "处理逾期任务",
            "href": "/agency",
            "task_title": f"跟进{tenant_name}逾期任务",
        }
    if readiness["missing_keys"]:
        missing_key = readiness["missing_keys"][0]
        step = next((item for item in READINESS_STEPS if item[0] == missing_key), READINESS_STEPS[0])
        return {
            "type": missing_key,
            "label": step[1],
            "href": step[2],
            "task_title": f"为{tenant_name}{step[1]}",
        }
    if task_summary["pending"] > 0 or task_summary["in_progress"] > 0:
        return {
            "type": "task",
            "label": "处理任务",
            "href": "/agency",
            "task_title": f"跟进{tenant_name}待办任务",
        }
    return {
        "type": "checklist",
        "label": "查看上线检查",
        "href": "/agency",
        "task_title": f"复核{tenant_name}上线检查",
    }


async def get_tenant_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """获取租户开通状态和初始化进度"""
    from app.models.tenant import Tenant

    # 产品数
    products_count = await db.execute(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id))
    products = products_count.scalar() or 0

    # 品牌数
    brands_count = await db.execute(select(func.count()).select_from(Brand).where(Brand.tenant_id == tenant_id))
    brands = brands_count.scalar() or 0

    # 已发布页面数
    published_pages = await db.execute(
        select(func.count())
        .select_from(PageVersion)
        .where(
            PageVersion.tenant_id == tenant_id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    pages = published_pages.scalar() or 0

    # 已激活码批次数
    from app.models.code import CodeBatchStatus

    activated_batches = await db.execute(
        select(func.count())
        .select_from(CodeBatch)
        .where(
            CodeBatch.tenant_id == tenant_id,
            CodeBatch.status == CodeBatchStatus.activated,
        )
    )
    activated = activated_batches.scalar() or 0

    # 已上线活动数
    active_campaigns = await db.execute(
        select(func.count())
        .select_from(Campaign)
        .where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == CampaignStatus.ACTIVE,
        )
    )
    campaigns = active_campaigns.scalar() or 0

    # 获取租户 onboarding_progress
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    onboarding_progress = tenant.onboarding_progress if tenant else {}
    if not onboarding_progress:
        onboarding_progress = {
            "brand_configured": brands >= 1,
            "product_created": products >= 1,
            "page_published": pages >= 1,
            "code_batch_activated": activated >= 1,
            "campaign_active": campaigns >= 1,
        }

    return {
        "tenant_id": str(tenant_id),
        "products": products,
        "brands": brands,
        "published_pages": pages,
        "activated_batches": activated,
        "active_campaigns": campaigns,
        "onboarding_progress": onboarding_progress,
    }


async def get_launch_checklist(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """获取上线检查清单"""
    status = await get_tenant_status(db, tenant_id)

    checks = [
        {
            "name": "至少 1 个产品已创建",
            "passed": status["products"] >= 1,
            "detail": f"当前产品数: {status['products']}",
        },
        {
            "name": "至少 1 个页面已发布",
            "passed": status["published_pages"] >= 1,
            "detail": f"当前已发布页面: {status['published_pages']}",
        },
        {
            "name": "至少 1 个码批次已激活",
            "passed": status["activated_batches"] >= 1,
            "detail": f"当前已激活批次: {status['activated_batches']}",
        },
        {
            "name": "至少 1 个品牌已创建",
            "passed": status["brands"] >= 1,
            "detail": f"当前品牌数: {status['brands']}",
        },
    ]

    all_passed = all(c["passed"] for c in checks)
    return {
        "tenant_id": str(tenant_id),
        "ready": all_passed,
        "checks": checks,
        "passed_count": sum(1 for c in checks if c["passed"]),
        "total_count": len(checks),
    }


async def get_ops_workbench(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    readiness: str = "all",
    task_status: str = "all",
    agency_tenant_id: uuid.UUID | None = None,
    _authorized_client_ids: list[uuid.UUID] | None = None,
    _authorized_client_scopes: dict[uuid.UUID, set[str]] | None = None,
    _controlled: bool = False,
) -> dict:
    if db.get_bind().dialect.name == "postgresql" and not _controlled:
        visible_scopes = {"products", "pages", "campaigns", "codes", "analytics"}
        authorized_client_scopes = (
            await get_authorized_client_scopes(db, agency_tenant_id, visible_scopes)
            if agency_tenant_id is not None
            else None
        )
        authorized_client_ids = list(authorized_client_scopes) if authorized_client_scopes is not None else None
        if agency_tenant_id is not None and not authorized_client_ids:
            return _empty_workbench(page, page_size)
        if agency_tenant_id is None:
            # Platform is an explicit cross-tenant control-plane principal.
            from sqlalchemy import text

            from app.core.database import control_session_factory

            async with control_session_factory() as control_db:
                await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                return await get_ops_workbench(
                    control_db,
                    page=page,
                    page_size=page_size,
                    q=q,
                    readiness=readiness,
                    task_status=task_status,
                    agency_tenant_id=None,
                    _authorized_client_ids=None,
                    _authorized_client_scopes=None,
                    _controlled=True,
                )

        # An agency may use its own RLS view only to resolve the current live
        # authorization keys.  Every customer record and task is then read in
        # a fresh transaction scoped to that customer.  Never aggregate agency
        # business data in a bypass/control session.
        from app.core.database import async_session_factory, set_session_tenant_context

        client_rows: list[dict] = []
        task_rows: list[dict] = []
        aggregate = _empty_workbench(1, page_size)["summary"]
        for client_tenant_id in authorized_client_ids or []:
            async with async_session_factory() as tenant_db:
                await set_session_tenant_context(tenant_db, client_tenant_id)
                result = await get_ops_workbench(
                    tenant_db,
                    page=1,
                    page_size=1,
                    q=q,
                    readiness=readiness,
                    task_status=task_status,
                    agency_tenant_id=None,
                    _authorized_client_ids=[client_tenant_id],
                    _authorized_client_scopes=authorized_client_scopes,
                    _controlled=True,
                )
            for key in aggregate:
                aggregate[key] += result["summary"][key]
            client_rows.extend(result["clients"])
            task_rows.extend(result["tasks"])

        client_rows.sort(key=lambda item: item["created_at"], reverse=True)
        task_rows.sort(
            key=lambda item: (
                not item["overdue"],
                item["priority"] != OpsTaskPriority.high.value,
                _as_aware_utc(item["due_date"]) if item["due_date"] else datetime.max.replace(tzinfo=UTC),
            )
        )
        start = (page - 1) * page_size
        return {
            "summary": aggregate,
            "clients": client_rows[start : start + page_size],
            "tasks": task_rows,
            "total": len(client_rows),
            "page": page,
            "page_size": page_size,
        }

    tenant_query = select(Tenant).where(Tenant.status != TenantStatus.terminated)
    count_query = select(func.count()).select_from(Tenant).where(Tenant.status != TenantStatus.terminated)

    # Filter by authorized clients if agency_tenant_id is provided
    authorized_client_ids = _authorized_client_ids
    authorized_client_scopes = _authorized_client_scopes
    if agency_tenant_id:
        visible_scopes = {"products", "pages", "campaigns", "codes", "analytics"}
        authorized_client_scopes = await get_authorized_client_scopes(db, agency_tenant_id, visible_scopes)
        authorized_client_ids = list(authorized_client_scopes)
        if not authorized_client_ids:
            return _empty_workbench(page, page_size)
    if authorized_client_ids is not None:
        tenant_query = tenant_query.where(Tenant.id.in_(authorized_client_ids))
        count_query = count_query.where(Tenant.id.in_(authorized_client_ids))

    if q:
        escaped = escape_like_pattern(q)
        tenant_query = tenant_query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))
        count_query = count_query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))

    total = (await db.execute(count_query)).scalar() or 0
    tenant_query = tenant_query.order_by(Tenant.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    tenants = list((await db.execute(tenant_query)).scalars().all())
    tenant_ids = [tenant.id for tenant in tenants]

    # Early return when no tenants found
    if not tenant_ids:
        return {
            "summary": {
                "total_clients": total,
                "active_clients": 0,
                "ready_clients": 0,
                "blocked_clients": 0,
                "pending_tasks": 0,
                "in_progress_tasks": 0,
                "overdue_tasks": 0,
            },
            "clients": [],
            "tasks": [],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # Batch aggregation queries (replaces N+1 get_tenant_status calls)
    from app.models.code import CodeBatchStatus

    brand_counts = dict(
        (
            await db.execute(
                select(Brand.tenant_id, func.count()).group_by(Brand.tenant_id).where(Brand.tenant_id.in_(tenant_ids))
            )
        ).all()
    )
    product_counts = dict(
        (
            await db.execute(
                select(Product.tenant_id, func.count())
                .group_by(Product.tenant_id)
                .where(Product.tenant_id.in_(tenant_ids))
            )
        ).all()
    )
    published_page_counts = dict(
        (
            await db.execute(
                select(PageVersion.tenant_id, func.count())
                .group_by(PageVersion.tenant_id)
                .where(PageVersion.tenant_id.in_(tenant_ids), PageVersion.status == PageVersionStatus.published)
            )
        ).all()
    )
    activated_batch_counts = dict(
        (
            await db.execute(
                select(CodeBatch.tenant_id, func.count())
                .group_by(CodeBatch.tenant_id)
                .where(CodeBatch.tenant_id.in_(tenant_ids), CodeBatch.status == CodeBatchStatus.activated)
            )
        ).all()
    )
    active_campaign_counts = dict(
        (
            await db.execute(
                select(Campaign.tenant_id, func.count())
                .group_by(Campaign.tenant_id)
                .where(Campaign.tenant_id.in_(tenant_ids), Campaign.status == CampaignStatus.ACTIVE)
            )
        ).all()
    )

    full_scope = {"products", "pages", "campaigns", "codes", "analytics"}
    full_access_ids = [
        tenant_id
        for tenant_id in tenant_ids
        if authorized_client_scopes is None or full_scope.issubset(authorized_client_scopes.get(tenant_id, set()))
    ]
    task_rows: list[OpsTask] = []
    if full_access_ids:
        task_result = await db.execute(
            select(OpsTask).where(OpsTask.tenant_id.in_(full_access_ids)).order_by(OpsTask.created_at.desc())
        )
        task_rows = list(task_result.scalars().all())

    tasks_by_tenant: dict[uuid.UUID, list[OpsTask]] = {tenant.id: [] for tenant in tenants}
    for task in task_rows:
        tasks_by_tenant.setdefault(task.tenant_id, []).append(task)

    now = datetime.now(UTC)
    clients = []
    ready_clients = 0
    blocked_clients = 0
    pending_tasks = 0
    in_progress_tasks = 0
    overdue_tasks = 0

    for tenant in tenants:
        tenant_scopes = (
            authorized_client_scopes.get(tenant.id, set()) if authorized_client_scopes is not None else full_scope
        )
        has_full_access = full_scope.issubset(tenant_scopes)
        status = {
            "tenant_id": str(tenant.id),
            "brands": brand_counts.get(tenant.id, 0),
            "products": product_counts.get(tenant.id, 0),
            "published_pages": published_page_counts.get(tenant.id, 0),
            "activated_batches": activated_batch_counts.get(tenant.id, 0),
            "active_campaigns": active_campaign_counts.get(tenant.id, 0),
            "onboarding_progress": tenant.onboarding_progress,
        }
        readiness_summary = (
            build_readiness_summary(status)
            if has_full_access
            else {
                "ready": False,
                "passed_count": 0,
                "total_count": 0,
                "percent": 0,
                "missing_keys": ["authorization_scope"],
                "missing_labels": ["当前授权仅允许进入指定业务模块"],
            }
        )
        tenant_tasks = tasks_by_tenant.get(tenant.id, [])
        task_summary = {
            "pending": 0,
            "in_progress": 0,
            "overdue": 0,
            "high_priority": 0,
        }

        for task in tenant_tasks:
            is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
            if task.status == OpsTaskStatus.pending:
                task_summary["pending"] += 1
                pending_tasks += 1
            if task.status == OpsTaskStatus.in_progress:
                task_summary["in_progress"] += 1
                in_progress_tasks += 1
            if _is_task_overdue(task, now):
                task_summary["overdue"] += 1
                overdue_tasks += 1
            if task.priority == OpsTaskPriority.high and is_open:
                task_summary["high_priority"] += 1

        if has_full_access:
            if readiness_summary["ready"]:
                ready_clients += 1
            else:
                blocked_clients += 1

        if readiness != "all" and not has_full_access:
            continue
        if readiness == "ready" and not readiness_summary["ready"]:
            continue
        if readiness == "blocked" and readiness_summary["ready"]:
            continue
        if task_status != "all" and task_summary.get(task_status, 0) == 0:
            continue

        clients.append(
            {
                "id": tenant.id,
                "name": tenant.name,
                "status": tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
                "plan": tenant.plan.value if hasattr(tenant.plan, "value") else str(tenant.plan),
                "plan_expires_at": tenant.plan_expires_at,
                "created_at": tenant.created_at,
                "readiness": readiness_summary,
                "task_summary": task_summary,
                "next_action": build_next_action(tenant.name, readiness_summary, task_summary),
                "agency_scope": sorted(tenant_scopes),
                "full_workbench_access": has_full_access,
            }
        )

    actionable_tasks = []
    for task in task_rows:
        is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
        if not is_open:
            continue
        tenant = next((item for item in tenants if item.id == task.tenant_id), None)
        actionable_tasks.append(
            {
                "id": task.id,
                "tenant_id": task.tenant_id,
                "tenant_name": tenant.name if tenant else None,
                "title": task.title,
                "description": task.description,
                "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                "due_date": task.due_date,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "overdue": _is_task_overdue(task, now),
            }
        )

    actionable_tasks.sort(
        key=lambda item: (
            not item["overdue"],
            item["priority"] != OpsTaskPriority.high.value,
            _as_aware_utc(item["due_date"]) if item["due_date"] else datetime.max.replace(tzinfo=UTC),
        )
    )

    return {
        "summary": {
            "total_clients": total,
            "active_clients": sum(1 for tenant in tenants if tenant.status == TenantStatus.active),
            "ready_clients": ready_clients,
            "blocked_clients": blocked_clients,
            "pending_tasks": pending_tasks,
            "in_progress_tasks": in_progress_tasks,
            "overdue_tasks": overdue_tasks,
        },
        "clients": clients,
        "tasks": actionable_tasks,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_pilot_aggregate(
    db: AsyncSession, agency_tenant_id: uuid.UUID | None = None, *, _controlled: bool = False
) -> dict:
    """代运营/平台跨租户试点聚合（beads: yimatong-bgag.10，PRD §4.5）。

    镜像 get_ops_workbench 的跨租户 RLS 纪律：agency 用自身 RLS session 仅解析授权 key，
    每个客户的里程碑/复盘在 fresh per-client session（set_session_tenant_context）内读取，
    绝不在 bypass/control session 聚合业务数据。platform 走 control-plane bypass
    （_controlled sentinel 防递归，与 get_ops_workbench 一致）。
    """
    from app.services.agency_auth import _load_tenant_labels
    from app.services.pilot_milestone import build_milestone_timeline
    from app.services.retrospective import _derived_status, list_retrospectives

    visible_scopes = {"products", "pages", "campaigns", "codes", "analytics"}
    is_pg = db.get_bind().dialect.name == "postgresql"

    if is_pg and agency_tenant_id is not None:
        authorized_client_scopes = await get_authorized_client_scopes(db, agency_tenant_id, visible_scopes)
        authorized_client_ids = list(authorized_client_scopes)
        if not authorized_client_ids:
            return {"summary": {"total_clients": 0, "clients_with_pending_retros": 0}, "clients": []}
        from app.core.database import async_session_factory, set_session_tenant_context

        now = datetime.now(UTC)
        client_summaries: list[dict] = []
        for client_tenant_id in authorized_client_ids:
            async with async_session_factory() as tenant_db:
                await set_session_tenant_context(tenant_db, client_tenant_id)
                timeline = await build_milestone_timeline(tenant_db, client_tenant_id)
                retros = await list_retrospectives(tenant_db, client_tenant_id, now)
            achieved = sum(1 for m in timeline.milestones if m.status == "achieved")
            pending = [
                {
                    "retro_id": r.id,
                    "period_day": r.period_day,
                    "next_review_date": _as_aware_utc(
                        datetime.combine(r.next_review_date, datetime.min.time(), tzinfo=UTC)
                    ),
                    "derived_status": _derived_status(r, now),
                }
                for r in retros
                if r.status != "completed"
            ]
            scopes = authorized_client_scopes.get(client_tenant_id, set())
            client_summaries.append(
                {
                    "client_id": client_tenant_id,
                    "milestone_summary": {"achieved_count": achieved, "total": len(timeline.milestones)},
                    "pending_retrospectives": pending,
                    "full_pilot_access": visible_scopes.issubset(scopes),
                }
            )
        labels = await _load_tenant_labels(db, {c["client_id"] for c in client_summaries})
        for c in client_summaries:
            name, slug = labels.get(c["client_id"], (None, None))
            c["client_name"] = name
            c["client_slug"] = slug
        with_pending = sum(1 for c in client_summaries if c["pending_retrospectives"])
        return {
            "summary": {"total_clients": len(client_summaries), "clients_with_pending_retros": with_pending},
            "clients": client_summaries,
        }

    # 平台（agency_tenant_id is None）：control-plane bypass 读取全部 active 租户。
    # _controlled sentinel 防递归（镜像 get_ops_workbench）：首次进入开 control session
    # 并 bypass RLS，二次进入（_controlled=True）落到下面的全量读取分支。
    if is_pg and agency_tenant_id is None and not _controlled:
        from sqlalchemy import text

        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            return await get_pilot_aggregate(control_db, agency_tenant_id=None, _controlled=True)

    # 全量读取分支：覆盖 (a) SQLite 测试（单 session，RLS no-op，靠 tenant_id 过滤手工隔离）
    # 与 (b) platform _controlled=True（control session 已 bypass RLS）。读全部 active 租户。
    tenant_rows = (
        await db.execute(select(Tenant.id, Tenant.name, Tenant.slug).where(Tenant.status == TenantStatus.active))
    ).all()
    now = datetime.now(UTC)
    client_summaries = []
    for tenant_id, name, slug in tenant_rows:
        timeline = await build_milestone_timeline(db, tenant_id)
        retros = await list_retrospectives(db, tenant_id, now)
        achieved = sum(1 for m in timeline.milestones if m.status == "achieved")
        pending = [
            {
                "retro_id": r.id,
                "period_day": r.period_day,
                "next_review_date": _as_aware_utc(
                    datetime.combine(r.next_review_date, datetime.min.time(), tzinfo=UTC)
                ),
                "derived_status": _derived_status(r, now),
            }
            for r in retros
            if r.status != "completed"
        ]
        client_summaries.append(
            {
                "client_id": tenant_id,
                "client_name": name,
                "client_slug": slug,
                "milestone_summary": {"achieved_count": achieved, "total": len(timeline.milestones)},
                "pending_retrospectives": pending,
                "full_pilot_access": True,
            }
        )
    with_pending = sum(1 for c in client_summaries if c["pending_retrospectives"])
    return {
        "summary": {"total_clients": len(client_summaries), "clients_with_pending_retros": with_pending},
        "clients": client_summaries,
    }
