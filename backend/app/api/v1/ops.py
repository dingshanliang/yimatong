"""代运营工作台 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_ops_user
from app.models.tenant import OpsTask, OpsTaskPriority, OpsTaskStatus, Tenant, TenantStatus
from app.schemas.common import PaginatedResponse
from app.schemas.tenant import OpsTaskCreate, OpsTaskRead, OpsTaskUpdate, OpsWorkbenchResponse
from app.services.ops import get_launch_checklist, get_ops_workbench, get_tenant_status

ops_router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


@ops_router.get("/overview", summary="代运营工作台概览")
async def get_ops_overview(
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    total_clients = (await db.execute(select(func.count()).select_from(Tenant))).scalar() or 0
    active_clients = (
        await db.execute(select(func.count()).select_from(Tenant).where(Tenant.status == TenantStatus.active))
    ).scalar() or 0
    onboarding_clients = (
        await db.execute(
            select(func.count())
            .select_from(Tenant)
            .where(Tenant.status == TenantStatus.active, Tenant.enabled_features.is_(None))
        )
    ).scalar() or 0
    pending_tasks = (
        await db.execute(select(func.count()).select_from(OpsTask).where(OpsTask.status == OpsTaskStatus.pending))
    ).scalar() or 0
    completed_tasks = (
        await db.execute(select(func.count()).select_from(OpsTask).where(OpsTask.status == OpsTaskStatus.completed))
    ).scalar() or 0
    return {
        "total_clients": total_clients,
        "active_clients": active_clients,
        "onboarding_clients": onboarding_clients,
        "ready_clients": max(active_clients - onboarding_clients, 0),
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
    }


@ops_router.get("/workbench", response_model=OpsWorkbenchResponse, summary="代运营工作台聚合")
async def get_ops_workbench_endpoint(
    _ops_user: tuple = Depends(get_ops_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索客户名称"),
    readiness: str = Query("all", pattern="^(all|ready|blocked)$"),
    task_status: str = Query("all", pattern="^(all|pending|in_progress|overdue)$"),
    db: AsyncSession = Depends(get_db),
):
    return await get_ops_workbench(
        db,
        page=page,
        page_size=page_size,
        q=q,
        readiness=readiness,
        task_status=task_status,
    )


@ops_router.get("/clients/{tenant_id}/status")
async def get_client_status(
    tenant_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_tenant_status(db, tenant_id)


@ops_router.get("/clients/{tenant_id}/launch-checklist", summary="获取 launch checklist")
async def get_launch_checklist_endpoint(
    tenant_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_launch_checklist(db, tenant_id)


@ops_router.post("/tasks", response_model=OpsTaskRead, status_code=201, summary="创建 任务")
async def create_task_endpoint(
    body: OpsTaskCreate,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    task = OpsTask(
        tenant_id=body.tenant_id,
        title=body.title,
        description=body.description,
        priority=OpsTaskPriority(body.priority) if body.priority else OpsTaskPriority.medium,
        due_date=body.due_date,
        assigned_to=body.assigned_to,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@ops_router.get("/tasks", response_model=PaginatedResponse, summary="任务 列表")
async def list_tasks_endpoint(
    _ops_user: tuple = Depends(get_ops_user),
    tenant_id: uuid.UUID | None = Query(None, description="按客户筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """待办任务列表（支持按客户和状态筛选）"""
    query = select(OpsTask, Tenant.name.label("tenant_name")).join(Tenant, Tenant.id == OpsTask.tenant_id)
    count_query = select(func.count()).select_from(OpsTask)

    if tenant_id:
        query = query.where(OpsTask.tenant_id == tenant_id)
        count_query = count_query.where(OpsTask.tenant_id == tenant_id)
    if status:
        query = query.where(OpsTask.status == status)
        count_query = count_query.where(OpsTask.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(OpsTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    return PaginatedResponse(
        items=[
            {
                **OpsTaskRead.model_validate(task).model_dump(mode="json"),
                "tenant_name": tenant_name,
            }
            for task, tenant_name in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@ops_router.get("/tasks/{task_id}", response_model=OpsTaskRead, summary="获取 任务")
async def get_task_endpoint(
    task_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OpsTask).where(OpsTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@ops_router.patch("/tasks/{task_id}", response_model=OpsTaskRead, summary="更新 任务")
async def update_task_endpoint(
    task_id: uuid.UUID,
    body: OpsTaskUpdate,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OpsTask).where(OpsTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.title is not None:
        task.title = body.title
    if body.description is not None:
        task.description = body.description
    if body.status is not None:
        task.status = OpsTaskStatus(body.status)
    if body.priority is not None:
        task.priority = OpsTaskPriority(body.priority)
    if body.due_date is not None:
        task.due_date = body.due_date

    await db.flush()
    await db.refresh(task)
    return task


@ops_router.delete("/tasks/{task_id}", status_code=204, summary="删除 任务")
async def delete_task_endpoint(
    task_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OpsTask).where(OpsTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.flush()
    return None
