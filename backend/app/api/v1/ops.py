"""代运营工作台 API"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import (
    async_session_factory,
    control_session_factory,
    get_db_for_ops,
    set_session_tenant_context,
)
from app.core.dependencies import get_ops_user
from app.models.tenant import OpsTask, OpsTaskPriority, OpsTaskStatus, Tenant
from app.schemas.common import PaginatedResponse
from app.schemas.tenant import OpsTaskCreate, OpsTaskRead, OpsTaskUpdate, OpsWorkbenchResponse
from app.services.agency_auth import get_authorized_client_ids
from app.services.audit import write_audit_log
from app.services.ops import get_launch_checklist, get_ops_workbench, get_tenant_status
from app.utils.auth_rbac import require_tenant_type

ops_router = APIRouter(
    prefix="/api/v1/ops",
    tags=["ops"],
    dependencies=[Depends(require_tenant_type("agency", "platform"))],
)

WORKBENCH_REQUIRED_SCOPES = {"products", "pages", "campaigns", "codes", "analytics"}


async def _allowed_client_ids(db: AsyncSession, ops_user: tuple) -> list[uuid.UUID] | None:
    _, _, tenant_id, tenant_type = ops_user
    if tenant_type == "platform":
        return None
    if tenant_id is None:
        return []
    return await get_authorized_client_ids(db, tenant_id, WORKBENCH_REQUIRED_SCOPES)


async def _require_client_access(db: AsyncSession, ops_user: tuple, client_tenant_id: uuid.UUID) -> None:
    allowed_ids = await _allowed_client_ids(db, ops_user)
    if allowed_ids is not None and client_tenant_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="当前代运营授权不包含该客户")


@asynccontextmanager
async def _client_business_session(
    request_db: AsyncSession,
    ops_user: tuple,
    client_tenant_id: uuid.UUID,
) -> AsyncIterator[AsyncSession]:
    """Open the correct database boundary for one ops customer."""
    if request_db.get_bind().dialect.name != "postgresql":
        # Unit tests use one transaction-scoped SQLite session; RLS boundary
        # switching is a PostgreSQL-only behavior.
        yield request_db
        return
    if ops_user[3] == "platform":
        from sqlalchemy import text

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            try:
                yield control_db
                await control_db.commit()
            except Exception:
                await control_db.rollback()
                raise
        return

    await _require_client_access(request_db, ops_user, client_tenant_id)
    async with async_session_factory() as tenant_db:
        await set_session_tenant_context(tenant_db, client_tenant_id)
        try:
            yield tenant_db
            await tenant_db.commit()
        except Exception:
            await tenant_db.rollback()
            raise


async def _resolve_task_tenant_id(request_db: AsyncSession, task_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve only the tenant key needed to enter a task's RLS transaction."""
    from sqlalchemy import text

    if request_db.get_bind().dialect.name != "postgresql":
        value = await request_db.scalar(select(OpsTask.tenant_id).where(OpsTask.id == task_id).limit(1))
        return uuid.UUID(str(value)) if value is not None else None
    async with control_session_factory() as control_db:
        await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        value = await control_db.scalar(select(OpsTask.tenant_id).where(OpsTask.id == task_id).limit(1))
    return uuid.UUID(str(value)) if value is not None else None


async def _write_ops_audit(
    *,
    request_db: AsyncSession,
    operator_id: str,
    tenant_id: uuid.UUID,
    action: str,
    resource: str,
    details: dict,
) -> None:
    """Write protected global audit state through the control plane only."""
    from sqlalchemy import text

    if request_db.get_bind().dialect.name != "postgresql":
        await write_audit_log(
            request_db,
            operator_id=operator_id,
            target_tenant_id=str(tenant_id),
            action=action,
            resource=resource,
            details=details,
        )
        return
    async with control_session_factory() as control_db:
        await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await write_audit_log(
            control_db,
            operator_id=operator_id,
            target_tenant_id=str(tenant_id),
            action=action,
            resource=resource,
            details=details,
        )
        await control_db.commit()


@ops_router.get("/overview", summary="代运营工作台概览")
async def get_ops_overview(
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    _, _, tenant_id, tenant_type = _ops_user
    result = await get_ops_workbench(
        db,
        page=1,
        page_size=100,
        agency_tenant_id=tenant_id if tenant_type == "agency" else None,
    )
    summary = result["summary"]
    return {
        "total_clients": summary["total_clients"],
        "active_clients": summary["active_clients"],
        "onboarding_clients": summary["blocked_clients"],
        "ready_clients": summary["ready_clients"],
        "pending_tasks": summary["pending_tasks"],
        "completed_tasks": 0,
    }


@ops_router.get("/workbench", response_model=OpsWorkbenchResponse, summary="代运营工作台聚合")
async def get_ops_workbench_endpoint(
    _ops_user: tuple = Depends(get_ops_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索客户名称"),
    readiness: str = Query("all", pattern="^(all|ready|blocked)$"),
    task_status: str = Query("all", pattern="^(all|pending|in_progress|overdue)$"),
    db: AsyncSession = Depends(get_db_for_ops),
):
    _, _, tenant_id, tenant_type = _ops_user
    agency_tenant_id = tenant_id if tenant_type == "agency" else None
    return await get_ops_workbench(
        db,
        page=page,
        page_size=page_size,
        q=q,
        readiness=readiness,
        task_status=task_status,
        agency_tenant_id=agency_tenant_id,
    )


@ops_router.get("/clients/{tenant_id}/status")
async def get_client_status(
    tenant_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    async with _client_business_session(db, _ops_user, tenant_id) as tenant_db:
        return await get_tenant_status(tenant_db, tenant_id)


@ops_router.get("/clients/{tenant_id}/launch-checklist", summary="获取 launch checklist")
async def get_launch_checklist_endpoint(
    tenant_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    async with _client_business_session(db, _ops_user, tenant_id) as tenant_db:
        return await get_launch_checklist(tenant_db, tenant_id)


@ops_router.post("/tasks", response_model=OpsTaskRead, status_code=201, summary="创建 任务")
async def create_task_endpoint(
    body: OpsTaskCreate,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    async with _client_business_session(db, _ops_user, body.tenant_id) as tenant_db:
        task = OpsTask(
            tenant_id=body.tenant_id,
            title=body.title,
            description=body.description,
            priority=OpsTaskPriority(body.priority) if body.priority else OpsTaskPriority.medium,
            due_date=body.due_date,
            assigned_to=body.assigned_to,
        )
        tenant_db.add(task)
        await tenant_db.flush()
        await tenant_db.refresh(task)
        after = OpsTaskRead.model_validate(task).model_dump(mode="json")
    await _write_ops_audit(
        request_db=db,
        operator_id=str(_ops_user[0]),
        tenant_id=task.tenant_id,
        action="ops_task_created",
        resource=f"ops_task:{task.id}",
        details={"after": after},
    )
    return task


@ops_router.get("/tasks", response_model=PaginatedResponse, summary="任务 列表")
async def list_tasks_endpoint(
    _ops_user: tuple = Depends(get_ops_user),
    tenant_id: uuid.UUID | None = Query(None, description="按客户筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_for_ops),
):
    """待办任务列表（支持按客户和状态筛选）"""
    allowed_ids = await _allowed_client_ids(db, _ops_user)
    if allowed_ids is not None:
        if tenant_id is not None and tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="当前代运营授权不包含该客户")
        selected_ids = [tenant_id] if tenant_id is not None else allowed_ids
        items: list[dict] = []
        for selected_id in selected_ids:
            async with _client_business_session(db, _ops_user, selected_id) as tenant_db:
                query = (
                    select(OpsTask, Tenant.name.label("tenant_name"))
                    .join(Tenant, Tenant.id == OpsTask.tenant_id)
                    .where(OpsTask.tenant_id == selected_id)
                )
                if status:
                    query = query.where(OpsTask.status == status)
                rows = (await tenant_db.execute(query.order_by(OpsTask.created_at.desc()))).all()
                items.extend(
                    {
                        **OpsTaskRead.model_validate(task).model_dump(mode="json"),
                        "tenant_name": tenant_name,
                    }
                    for task, tenant_name in rows
                )
        items.sort(key=lambda item: item["created_at"], reverse=True)
        start = (page - 1) * page_size
        return PaginatedResponse(
            items=items[start : start + page_size],
            total=len(items),
            page=page,
            page_size=page_size,
        )

    # Platform is the explicit cross-tenant control-plane caller.
    from sqlalchemy import text

    if db.get_bind().dialect.name != "postgresql":
        query_db = db
        query = select(OpsTask, Tenant.name.label("tenant_name")).join(Tenant, Tenant.id == OpsTask.tenant_id)
        count_query = select(func.count()).select_from(OpsTask)
        if tenant_id:
            query = query.where(OpsTask.tenant_id == tenant_id)
            count_query = count_query.where(OpsTask.tenant_id == tenant_id)
        if status:
            query = query.where(OpsTask.status == status)
            count_query = count_query.where(OpsTask.status == status)
        total = (await query_db.execute(count_query)).scalar() or 0
        query = query.order_by(OpsTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = (await query_db.execute(query)).all()
    else:
        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            query = select(OpsTask, Tenant.name.label("tenant_name")).join(Tenant, Tenant.id == OpsTask.tenant_id)
            count_query = select(func.count()).select_from(OpsTask)
            if tenant_id:
                query = query.where(OpsTask.tenant_id == tenant_id)
                count_query = count_query.where(OpsTask.tenant_id == tenant_id)
            if status:
                query = query.where(OpsTask.status == status)
                count_query = count_query.where(OpsTask.status == status)
            total = (await control_db.execute(count_query)).scalar() or 0
            query = query.order_by(OpsTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            rows = (await control_db.execute(query)).all()

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
    db: AsyncSession = Depends(get_db_for_ops),
):
    task_tenant_id = await _resolve_task_tenant_id(db, task_id)
    if task_tenant_id is None:
        raise HTTPException(status_code=404, detail="Task not found")
    async with _client_business_session(db, _ops_user, task_tenant_id) as tenant_db:
        task = await tenant_db.scalar(select(OpsTask).where(OpsTask.id == task_id, OpsTask.tenant_id == task_tenant_id))
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task


@ops_router.patch("/tasks/{task_id}", response_model=OpsTaskRead, summary="更新 任务")
async def update_task_endpoint(
    task_id: uuid.UUID,
    body: OpsTaskUpdate,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    task_tenant_id = await _resolve_task_tenant_id(db, task_id)
    if task_tenant_id is None:
        raise HTTPException(status_code=404, detail="Task not found")
    async with _client_business_session(db, _ops_user, task_tenant_id) as tenant_db:
        task = await tenant_db.scalar(
            select(OpsTask).where(OpsTask.id == task_id, OpsTask.tenant_id == task_tenant_id).with_for_update()
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        before = OpsTaskRead.model_validate(task).model_dump(mode="json")
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
        await tenant_db.flush()
        await tenant_db.refresh(task)
        after = OpsTaskRead.model_validate(task).model_dump(mode="json")
    await _write_ops_audit(
        request_db=db,
        operator_id=str(_ops_user[0]),
        tenant_id=task_tenant_id,
        action="ops_task_updated",
        resource=f"ops_task:{task.id}",
        details={"before": before, "after": after},
    )
    return task


@ops_router.delete("/tasks/{task_id}", status_code=204, summary="删除 任务")
async def delete_task_endpoint(
    task_id: uuid.UUID,
    _ops_user: tuple = Depends(get_ops_user),
    db: AsyncSession = Depends(get_db_for_ops),
):
    task_tenant_id = await _resolve_task_tenant_id(db, task_id)
    if task_tenant_id is None:
        raise HTTPException(status_code=404, detail="Task not found")
    async with _client_business_session(db, _ops_user, task_tenant_id) as tenant_db:
        task = await tenant_db.scalar(
            select(OpsTask).where(OpsTask.id == task_id, OpsTask.tenant_id == task_tenant_id).with_for_update()
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        before = OpsTaskRead.model_validate(task).model_dump(mode="json")
        await tenant_db.delete(task)
        await tenant_db.flush()
    await _write_ops_audit(
        request_db=db,
        operator_id=str(_ops_user[0]),
        tenant_id=task_tenant_id,
        action="ops_task_deleted",
        resource=f"ops_task:{task.id}",
        details={"before": before, "after": "deleted"},
    )
    return None
