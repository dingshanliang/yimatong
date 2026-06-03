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
from app.services.agency_auth import get_authorized_client_ids
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
    progress = status.get("onboarding_progress") or {}
    derived = {
        "brand_configured": status.get("brands", 0) >= 1,
        "product_created": status.get("products", 0) >= 1,
        "page_published": status.get("published_pages", 0) >= 1,
        "code_batch_activated": status.get("activated_batches", 0) >= 1,
    }
    merged = {**derived, **progress}
    missing = [(key, label, href) for key, label, href in READINESS_STEPS if not merged.get(key)]
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
            CodeBatch.status == CodeBatchStatus.completed,
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
) -> dict:
    tenant_query = select(Tenant).where(Tenant.status != TenantStatus.terminated)
    count_query = select(func.count()).select_from(Tenant).where(Tenant.status != TenantStatus.terminated)

    # Filter by authorized clients if agency_tenant_id is provided
    authorized_client_ids: list[uuid.UUID] | None = None
    if agency_tenant_id:
        authorized_client_ids = await get_authorized_client_ids(db, agency_tenant_id)
        if not authorized_client_ids:
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
                .where(CodeBatch.tenant_id.in_(tenant_ids), CodeBatch.status == CodeBatchStatus.completed)
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

    task_rows: list[OpsTask] = []
    if tenant_ids:
        task_result = await db.execute(
            select(OpsTask).where(OpsTask.tenant_id.in_(tenant_ids)).order_by(OpsTask.created_at.desc())
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
        status = {
            "tenant_id": str(tenant.id),
            "brands": brand_counts.get(tenant.id, 0),
            "products": product_counts.get(tenant.id, 0),
            "published_pages": published_page_counts.get(tenant.id, 0),
            "activated_batches": activated_batch_counts.get(tenant.id, 0),
            "active_campaigns": active_campaign_counts.get(tenant.id, 0),
            "onboarding_progress": tenant.onboarding_progress,
        }
        readiness_summary = build_readiness_summary(status)
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

        if readiness_summary["ready"]:
            ready_clients += 1
        else:
            blocked_clients += 1

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
