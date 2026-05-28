"""码批次和码项 API"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.code import CodeItemStatus, CodeType
from app.services.code import (
    activate_batch,
    bind_code_item,
    create_code_batch,
    get_code_batch,
    get_code_item,
    list_code_batches,
    list_code_items,
    resolve_code_by_public_id,
    revoke_code_item,
)
from app.services.code_export import enqueue_export_task

code_batch_router = APIRouter(prefix="/api/v1/code-batches", tags=["code-batches"])
code_item_router = APIRouter(prefix="/api/v1/code-items", tags=["code-items"])


class CodeBatchCreateRequest(BaseModel):
    product_id: uuid.UUID
    sku_id: uuid.UUID
    batch_code: str
    quantity: int
    code_type: str = CodeType.single


class CodeBatchRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    product_id: uuid.UUID
    sku_id: uuid.UUID
    batch_code: str
    quantity: int
    status: str
    code_type: str = CodeType.single
    created_by: uuid.UUID

    model_config = {"from_attributes": True}


class CodeItemRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    code_batch_id: uuid.UUID
    public_id: str
    status: CodeItemStatus
    code_type: str = CodeType.single
    pair_id: uuid.UUID | None = None
    activated_at: datetime | None = None
    bound_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


@code_batch_router.post("", status_code=201)
async def create_code_batch_endpoint(
    body: CodeBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    from app.services.quota import check_quota, QuotaExceededError
    from app.models.tenant import Tenant

    tenant = await db.get(Tenant, tenant_id)
    if tenant and tenant.quota:
        try:
            check_quota(tenant.quota, "max_codes_per_batch", body.quantity)
        except QuotaExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))

    return await create_code_batch(
        db, tenant_id, body.product_id, body.sku_id,
        body.batch_code, body.quantity, account_id,
        code_type=body.code_type,
    )


@code_batch_router.get("")
async def list_code_batches_endpoint(
    product_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_code_batches(
        db, tenant_id, product_id=product_id, status=status, page=page, page_size=page_size,
    )
    return PaginatedResponse(
        items=[CodeBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@code_batch_router.get("/{batch_id}")
async def get_code_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batch = await get_code_batch(db, tenant_id, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Code batch not found")
    return batch


@code_batch_router.post("/{batch_id}/activate")
async def activate_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await activate_batch(db, tenant_id, batch_id)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@code_batch_router.post("/{batch_id}/export", status_code=202)
async def export_code_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    task_id = await enqueue_export_task(tenant_id, batch_id, account_id)
    return {"task_id": task_id}


@code_item_router.get("/public/{public_id}")
async def resolve_code_by_public_id_endpoint(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await resolve_code_by_public_id(db, tenant_id, public_id)
    if not data:
        raise HTTPException(status_code=404, detail="Code not found")
    if data["status"] == CodeItemStatus.revoked:
        raise HTTPException(status_code=410, detail="Code has been revoked")
    return data


@code_item_router.get("/{item_id}")
async def get_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    item = await get_code_item(db, tenant_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Code item not found")
    return CodeItemRead.model_validate(item)


@code_item_router.get("/{item_id}/pair")
async def get_pair_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    item = await get_code_item(db, tenant_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Code item not found")
    if not item.pair_id:
        raise HTTPException(status_code=404, detail="This code has no pair")

    from sqlalchemy import select as sql_select

    from app.models.code import CodeItem as CodeItemModel

    result = await db.execute(
        sql_select(CodeItemModel).where(
            CodeItemModel.pair_id == item.pair_id,
            CodeItemModel.id != item.id,
            CodeItemModel.tenant_id == tenant_id,
        )
    )
    pair = result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair code not found")
    return CodeItemRead.model_validate(pair)


@code_item_router.post("/{item_id}/revoke", response_model=CodeItemRead)
async def revoke_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await revoke_code_item(db, tenant_id, item_id)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@code_item_router.post("/{item_id}/bind", response_model=CodeItemRead)
async def bind_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await bind_code_item(db, tenant_id, item_id)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@code_item_router.get("")
async def list_code_items_endpoint(
    code_batch_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_code_items(
        db, tenant_id, code_batch_id=code_batch_id, status=status, page=page, page_size=page_size,
    )
    return PaginatedResponse(
        items=[CodeItemRead.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )
