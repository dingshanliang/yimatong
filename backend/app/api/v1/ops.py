"""代运营工作台 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.tenant import OpsTask, OpsTaskPriority, OpsTaskStatus
from app.schemas.common import PaginatedResponse
from app.schemas.tenant import OpsTaskCreate, OpsTaskRead, OpsTaskUpdate
from app.services.ops import get_launch_checklist, get_tenant_status

ops_router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


@ops_router.get("/clients/{tenant_id}/status")
async def get_client_status(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_tenant: uuid.UUID = Depends(get_current_tenant),
):
    return await get_tenant_status(db, tenant_id)


@ops_router.get("/clients/{tenant_id}/launch-checklist")
async def get_launch_checklist_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_tenant: uuid.UUID = Depends(get_current_tenant),
):
    return await get_launch_checklist(db, tenant_id)


@ops_router.post("/tasks", response_model=OpsTaskRead, status_code=201)
async def create_task_endpoint(
    body: OpsTaskCreate,
    db: AsyncSession = Depends(get_db),
):
    task = OpsTask(
        tenant_id=body.tenant_id,
        title=body.title,
        description=body.description,
        priority=OpsTaskPriority(body.priority) if body.priority else OpsTaskPriority.medium,
        due_date=body.due_date,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@ops_router.get("/tasks", response_model=PaginatedResponse)
async def list_tasks_endpoint(
    tenant_id: uuid.UUID | None = Query(None, description="按客户筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """待办任务列表（支持按客户和状态筛选）"""
    query = select(OpsTask)
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
    items = result.scalars().all()

    return PaginatedResponse(
        items=[OpsTaskRead.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@ops_router.get("/tasks/{task_id}", response_model=OpsTaskRead)
async def get_task_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OpsTask).where(OpsTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@ops_router.patch("/tasks/{task_id}", response_model=OpsTaskRead)
async def update_task_endpoint(
    task_id: uuid.UUID,
    body: OpsTaskUpdate,
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


@ops_router.delete("/tasks/{task_id}", status_code=204)
async def delete_task_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OpsTask).where(OpsTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.flush()
    return None
