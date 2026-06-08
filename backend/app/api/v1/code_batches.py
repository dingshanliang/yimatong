"""码批次和码项 API"""

import io
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.core.exceptions import BadRequestError
from app.models.code import CodeGenerationMode, CodeItemStatus, CodeType
from app.schemas.common import PaginatedResponse
from app.services.batch_state import InvalidBatchStateTransitionError
from app.services.code import (
    activate_batch,
    bind_code_item,
    create_code_batch,
    freeze_batch,
    get_code_batch,
    get_code_item,
    list_code_batches,
    list_code_items,
    mark_delivered,
    mark_printing,
    resolve_code_by_public_id,
    revoke_code_item,
    update_batch,
    update_code_item,
    void_batch,
)
from app.services.code_export import generate_code_csv
from app.utils.auth_rbac import require_permission

code_batch_router = APIRouter(prefix="/api/v1/code-batches", tags=["code-batches"])
code_item_router = APIRouter(prefix="/api/v1/code-items", tags=["code-items"])


class CodeBatchCreateRequest(BaseModel):
    product_id: uuid.UUID
    sku_id: uuid.UUID
    production_batch_id: uuid.UUID
    batch_code: str | None = None
    quantity: int
    code_type: str = CodeType.single
    generation_mode: CodeGenerationMode = CodeGenerationMode.item_level


class CodeBatchRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    product_id: uuid.UUID
    sku_id: uuid.UUID
    production_batch_id: uuid.UUID | None = None
    batch_code: str
    quantity: int
    status: str
    code_type: str = CodeType.single
    generation_mode: str = CodeGenerationMode.item_level
    created_by: uuid.UUID
    product_name: str | None = None
    sku_name: str | None = None
    sku_code: str | None = None
    production_batch_code: str | None = None
    production_date: date | None = None
    production_origin: str | None = None
    created_at: datetime | None = None

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
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


@code_batch_router.post("", status_code=201, summary="创建 码批次")
async def create_code_batch_endpoint(
    body: CodeBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:generate")),
):
    from app.models.tenant import Tenant
    from app.services.quota import QuotaExceededError, check_quota

    tenant = await db.get(Tenant, tenant_id)
    generation_quantity = 1 if body.generation_mode == CodeGenerationMode.batch_level else body.quantity
    if tenant and tenant.quota:
        try:
            check_quota(tenant.quota, "max_codes_per_batch", generation_quantity)
        except QuotaExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))

    try:
        return await create_code_batch(
            db,
            tenant_id,
            body.product_id,
            body.sku_id,
            body.production_batch_id,
            body.quantity,
            account_id,
            code_type=body.code_type,
            generation_mode=body.generation_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@code_batch_router.get("", summary="码批次 列表")
async def list_code_batches_endpoint(
    product_id: uuid.UUID | None = Query(None),
    sku_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_code_batches(
        db,
        tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[CodeBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@code_batch_router.get("/{batch_id}", summary="获取 码批次")
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
    _: None = Depends(require_permission("code:manage")),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await activate_batch(db, tenant_id, batch_id)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@code_batch_router.post("/{batch_id}/export", summary="导出 码批次")
async def export_code_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:export")),
):
    from app.core.exceptions import NotFoundError

    try:
        csv_content = await generate_code_csv(db, tenant_id, batch_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail) from e
    # 记录导出审计日志
    from app.services.export_audit import log_export

    await log_export(
        db,
        tenant_id,
        account_id,
        "code_csv",
        resource_id=str(batch_id),
        file_name=f"codes-{batch_id}.csv",
        row_count=csv_content.count("\n") - 1,
    )
    await db.commit()
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=codes-{batch_id}.csv"},
    )


class CodeBatchUpdateRequest(BaseModel):
    batch_code: str | None = None


@code_batch_router.patch("/{batch_id}", summary="更新 码批次")
async def update_code_batch_endpoint(
    batch_id: uuid.UUID,
    body: CodeBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):

    result = await update_batch(
        db,
        tenant_id,
        batch_id,
        batch_code=body.batch_code,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Code batch not found")
    return result


@code_batch_router.post("/{batch_id}/freeze")
async def freeze_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("code:manage")),
):
    return await freeze_batch(db, tenant_id, batch_id)


@code_batch_router.post("/{batch_id}/void")
async def void_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("code:manage")),
):
    return await void_batch(db, tenant_id, batch_id)


@code_batch_router.post("/{batch_id}/mark-printing", summary="标记印刷中")
async def mark_printing_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        result = await mark_printing(db, tenant_id, batch_id)
        return {"status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidBatchStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@code_batch_router.post("/{batch_id}/mark-delivered", summary="标记已交付")
async def mark_delivered_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        result = await mark_delivered(db, tenant_id, batch_id)
        return {"status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidBatchStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@code_item_router.get("/public/{public_id}", summary="解析 code by public id")
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


class CodeItemUpdateRequest(BaseModel):
    status: str | None = None


@code_item_router.patch("/{item_id}", response_model=CodeItemRead, summary="更新 码项")
async def update_code_item_endpoint(
    item_id: uuid.UUID,
    body: CodeItemUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("code:manage")),
):
    try:
        item = await update_code_item(db, tenant_id, item_id, status=body.status)
    except BadRequestError as e:
        raise HTTPException(status_code=400, detail=e.detail) from e
    if not item:
        raise HTTPException(status_code=404, detail="Code item not found")
    return CodeItemRead.model_validate(item)


@code_item_router.get("/{item_id}", summary="获取 码项")
async def get_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    item = await get_code_item(db, tenant_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Code item not found")
    return CodeItemRead.model_validate(item)


@code_item_router.get("/{item_id}/pair", summary="获取 pair")
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
    _: None = Depends(require_permission("code:manage")),
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
    _: None = Depends(require_permission("code:manage")),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await bind_code_item(db, tenant_id, item_id)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@code_item_router.get("", summary="码项 列表")
async def list_code_items_endpoint(
    code_batch_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_code_items(
        db,
        tenant_id,
        code_batch_id=code_batch_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[CodeItemRead.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )
