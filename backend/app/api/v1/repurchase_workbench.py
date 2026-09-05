import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.repurchase_workbench import (
    RepurchaseWorkItemAction,
    RepurchaseWorkItemAssignment,
    RepurchaseWorkItemCreate,
    RepurchaseWorkItemTransition,
)
from app.services.channel_access import require_brand_channel_principal
from app.services.repurchase_workbench import build_repurchase_workbench, mutate_work_item
from app.utils import utcnow
from app.utils.auth_rbac import require_permission

repurchase_workbench_router = APIRouter(prefix="/api/v1/members/repurchase-workbench", tags=["repurchase-workbench"])
_READ = [Depends(require_brand_channel_principal), Depends(require_permission("consumer:detail"))]
_WRITE = [Depends(require_brand_channel_principal), Depends(require_permission("campaign:write"))]


def _session_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="durable auth session required") from exc


@repurchase_workbench_router.get("", dependencies=_READ)
async def workbench_endpoint(
    time_basis: Literal["occurrence", "cohort"] = "occurrence",
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    end = end_at or utcnow()
    start = start_at or end - timedelta(days=30)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(status_code=422, detail="invalid_workbench_time_range")
    return await build_repurchase_workbench(db, tenant_id, start_at=start, end_at=end, time_basis=time_basis)


@repurchase_workbench_router.post("/work-items", status_code=201, dependencies=_WRITE)
async def create_work_item_endpoint(
    request: Request,
    body: RepurchaseWorkItemCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    item_id = await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=actor_id,
        auth_session_id=_session_id(request),
        payload={"action": "create", **body.model_dump()},
    )
    return {"id": item_id, "status": "pending"}


@repurchase_workbench_router.patch("/work-items/{item_id}", dependencies=_WRITE)
async def transition_work_item_endpoint(
    item_id: uuid.UUID,
    request: Request,
    body: RepurchaseWorkItemTransition,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=actor_id,
        auth_session_id=_session_id(request),
        payload={"action": "transition", "work_item_id": item_id, **body.model_dump()},
    )
    return {"id": item_id, "status": body.status}


@repurchase_workbench_router.patch("/work-items/{item_id}/assignment", dependencies=_WRITE)
async def assign_work_item_endpoint(
    item_id: uuid.UUID,
    request: Request,
    body: RepurchaseWorkItemAssignment,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=actor_id,
        auth_session_id=_session_id(request),
        payload={"action": "reassign", "work_item_id": item_id, **body.model_dump()},
    )
    return {"id": item_id, "owner_account_id": body.owner_account_id, "due_at": body.due_at}


@repurchase_workbench_router.post("/work-items/{item_id}/actions", status_code=202, dependencies=_WRITE)
async def work_item_action_endpoint(
    item_id: uuid.UUID,
    request: Request,
    body: RepurchaseWorkItemAction,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=actor_id,
        auth_session_id=_session_id(request),
        payload={"work_item_id": item_id, **body.model_dump()},
    )
    return {"id": item_id, "action": body.action, "accepted": True}
