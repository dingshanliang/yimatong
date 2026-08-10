"""码批次和码项 API"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.core.exceptions import BadRequestError
from app.models.code import CodeBatchSource, CodeGenerationMode, CodeItemStatus, CodeType
from app.schemas.common import PaginatedResponse
from app.services.batch_state import InvalidBatchStateTransitionError
from app.services.code import (
    activate_batch,
    bind_code_item,
    create_code_batch,
    enforce_code_operation_rate_limit,
    freeze_batch,
    get_code_batch,
    get_code_item,
    list_code_batches,
    list_code_items,
    map_code_batch_db_error,
    map_code_lifecycle_db_error,
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


def _raise_mapped_code_batch_db_error(exc: DBAPIError) -> None:
    mapped = map_code_batch_db_error(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


def _raise_mapped_code_lifecycle_db_error(exc: DBAPIError) -> None:
    mapped = map_code_lifecycle_db_error(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


CanonicalIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]


class CodeBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_id: uuid.UUID
    sku_id: uuid.UUID
    production_batch_id: uuid.UUID
    batch_code: str | None = Field(None, min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=10_000)
    code_type: CodeType = CodeType.single
    generation_mode: CodeGenerationMode = CodeGenerationMode.item_level
    source: CodeBatchSource = CodeBatchSource.generated

    @model_validator(mode="after")
    def validate_physical_item_limit(self) -> "CodeBatchCreateRequest":
        if self.code_type not in {CodeType.single, CodeType.paired}:
            raise ValueError("code_type must be single or paired")
        if (
            self.generation_mode == CodeGenerationMode.item_level
            and self.code_type == CodeType.paired
            and self.quantity > 5_000
        ):
            raise ValueError("paired code batches cannot exceed 5,000 pairs")
        if self.source == CodeBatchSource.imported and (
            self.code_type != CodeType.single or self.generation_mode != CodeGenerationMode.item_level
        ):
            raise ValueError("imported code batches must use item-level single codes")
        return self


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
    source: str = CodeBatchSource.generated
    expected_item_count: int
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


class CodeItemVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=200)
    confirm: Literal["void"]


class CodeBatchFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=200)
    confirm: Literal["freeze"]


@code_batch_router.post("", status_code=201, summary="创建 码批次")
async def create_code_batch_endpoint(
    body: CodeBatchCreateRequest,
    idempotency_key: CanonicalIdempotencyKey,
    _admission: None = Depends(enforce_code_operation_rate_limit),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:generate")),
):
    from app.services.quota import QuotaExceededError

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
            batch_code=body.batch_code,
            idempotency_key=idempotency_key,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except DBAPIError as exc:
        _raise_mapped_code_batch_db_error(exc)


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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await activate_batch(db, tenant_id, batch_id, actor_id=str(account_id))
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@code_batch_router.post("/{batch_id}/export", summary="导出 码批次")
async def export_code_batch_endpoint(
    batch_id: uuid.UUID,
    _admission: None = Depends(enforce_code_operation_rate_limit),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:export")),
):
    try:
        artifact = await generate_code_csv(db, tenant_id, batch_id, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DBAPIError as exc:
        _raise_mapped_code_batch_db_error(exc)
    return Response(
        content=artifact.content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=codes-{batch_id}.csv",
            "X-Code-Manifest-Version": str(artifact.manifest_version),
            "X-Code-Item-Count": str(artifact.row_count),
            "X-Content-SHA256": artifact.checksum_sha256,
        },
    )


class CodeBatchUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    batch_code: str | None = Field(None, min_length=1, max_length=100)


class CodeBatchDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)
    recipient: str = Field(min_length=1, max_length=255)
    confirm: Literal["deliver"]


@code_batch_router.patch("/{batch_id}", summary="更新 码批次")
async def update_code_batch_endpoint(
    batch_id: uuid.UUID,
    body: CodeBatchUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):

    try:
        result = await update_batch(
            db,
            tenant_id,
            batch_id,
            actor_id=str(account_id),
            batch_code=body.batch_code,
        )
    except DBAPIError as exc:
        _raise_mapped_code_batch_db_error(exc)
    if not result:
        raise HTTPException(status_code=404, detail="Code batch not found")
    return result


@code_batch_router.post("/{batch_id}/freeze")
async def freeze_batch_endpoint(
    batch_id: uuid.UUID,
    body: CodeBatchFreezeRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    try:
        return await freeze_batch(
            db,
            tenant_id,
            batch_id,
            actor_id=str(account_id),
            reason=body.reason,
        )
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@code_batch_router.post("/{batch_id}/void")
async def void_batch_endpoint(
    batch_id: uuid.UUID,
    # yimatong-zgb1.8 AC2：作废是受保护的不可逆动作，必须 reason + 二次确认（User Story 28）
    reason: str = "",
    confirm: Literal["void"] | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    # yimatong-zgb1.8：作废必须提供原因
    reason = reason.strip()
    if not reason or len(reason) > 200:
        raise HTTPException(status_code=422, detail="作废必须提供原因（reason 参数）")
    # 二次确认：confirm 必须等于 "void"（防止误操作）
    if confirm != "void":
        raise HTTPException(
            status_code=422,
            detail="作废是不可逆操作，必须传 confirm=void 进行二次确认",
        )
    try:
        return await void_batch(db, tenant_id, batch_id, actor_id=str(account_id), reason=reason)
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@code_batch_router.post("/{batch_id}/mark-printing", summary="标记印刷中")
async def mark_printing_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    try:
        result = await mark_printing(db, tenant_id, batch_id, actor_id=str(account_id))
        return {"status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidBatchStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except DBAPIError as exc:
        _raise_mapped_code_batch_db_error(exc)


@code_batch_router.post("/{batch_id}/mark-delivered", summary="标记已交付")
async def mark_delivered_endpoint(
    batch_id: uuid.UUID,
    body: CodeBatchDeliveryRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    try:
        result = await mark_delivered(
            db,
            tenant_id,
            batch_id,
            actor_id=str(account_id),
            reason=body.reason,
            recipient=body.recipient,
            confirm=body.confirm,
        )
        return {"status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidBatchStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except DBAPIError as exc:
        _raise_mapped_code_batch_db_error(exc)


@code_item_router.get("/public/{public_id}", summary="解析 code by public id")
async def resolve_code_by_public_id_endpoint(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("code:export")),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    _: None = Depends(require_permission("code:export")),
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
    _: None = Depends(require_permission("code:export")),
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
    body: CodeItemVoidRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await revoke_code_item(
            db,
            tenant_id,
            item_id,
            actor_id=str(account_id),
            reason=body.reason,
        )
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@code_item_router.post("/{item_id}/bind", response_model=CodeItemRead)
async def bind_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("code:manage")),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        return await bind_code_item(db, tenant_id, item_id, actor_id=str(account_id))
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@code_item_router.get("", summary="码项 列表")
async def list_code_items_endpoint(
    code_batch_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("code:export")),
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
