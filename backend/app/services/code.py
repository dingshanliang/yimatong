"""码管理服务层"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.code import (
    CodeBatch,
    CodeBatchStatus,
    CodeGenerationMode,
    CodeItem,
    CodeItemStatus,
    CodeType,
)
from app.models.product import SKU, Product, ProductionBatch
from app.schemas.code import (
    CodeBatchActivateResponse,
    CodeBatchDetailRead,
    CodeBatchFreezeResponse,
    CodeBatchVoidResponse,
)
from app.services.public_id import generate_public_id
from app.utils import utcnow


async def create_code_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    production_batch_id: uuid.UUID,
    quantity: int,
    created_by: uuid.UUID,
    code_type: str = CodeType.single,
    generation_mode: str = CodeGenerationMode.item_level,
) -> dict:
    product = await db.get(Product, product_id)
    if not product or product.tenant_id != tenant_id:
        raise ValueError("Product not found")

    sku = await db.get(SKU, sku_id)
    if not sku or sku.tenant_id != tenant_id:
        raise ValueError("SKU not found")
    if sku.product_id != product_id:
        raise ValueError("SKU does not belong to selected product")

    production_batch = await db.get(ProductionBatch, production_batch_id)
    if not production_batch or production_batch.tenant_id != tenant_id:
        raise ValueError("Production batch not found")
    if production_batch.product_id != product_id or production_batch.sku_id != sku_id:
        raise ValueError("Production batch does not belong to selected product and SKU")

    if generation_mode == CodeGenerationMode.batch_level:
        quantity = 1
        code_type = CodeType.single
    elif generation_mode != CodeGenerationMode.item_level:
        raise ValueError("Invalid generation mode")

    batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        production_batch_id=production_batch_id,
        batch_code=production_batch.batch_code,
        quantity=quantity,
        created_by=created_by,
        code_type=code_type,
        generation_mode=generation_mode,
    )
    db.add(batch)
    await db.flush()

    batch_size = 5000
    total_generated = 0

    def _build_items():
        used_ids: set[str] = set()

        def _unique_public_id() -> str:
            for _ in range(10):
                pid = generate_public_id()
                if pid not in used_ids:
                    used_ids.add(pid)
                    return pid
            raise RuntimeError("Failed to generate unique public_id after 10 retries")

        if code_type == CodeType.paired:
            for _ in range(quantity):
                pair_id = uuid.uuid4()
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=_unique_public_id(),
                    code_type=CodeType.outer,
                    pair_id=pair_id,
                )
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=_unique_public_id(),
                    code_type=CodeType.inner,
                    pair_id=pair_id,
                )
        else:
            for _ in range(quantity):
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=_unique_public_id(),
                    code_type=CodeType.single,
                )

    batch_items = []
    for item in _build_items():
        batch_items.append(item)
        if len(batch_items) >= batch_size:
            db.add_all(batch_items)
            await db.flush()
            total_generated += len(batch_items)
            batch_items = []
    if batch_items:
        db.add_all(batch_items)
        total_generated += len(batch_items)

    batch.status = CodeBatchStatus.completed
    await db.flush()
    await db.refresh(batch)

    return {
        "id": str(batch.id),
        "tenant_id": str(batch.tenant_id),
        "product_id": str(batch.product_id),
        "sku_id": str(batch.sku_id),
        "production_batch_id": str(batch.production_batch_id) if batch.production_batch_id else None,
        "batch_code": batch.batch_code,
        "quantity": batch.quantity,
        "generated_count": total_generated,
        "status": batch.status,
        "code_type": batch.code_type,
        "generation_mode": batch.generation_mode,
        "created_by": str(batch.created_by),
        "product_name": product.name,
        "sku_name": sku.name,
        "sku_code": sku.code,
        "production_batch_code": production_batch.batch_code,
        "production_date": production_batch.production_date,
        "production_origin": production_batch.origin,
    }


async def list_code_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    sku_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeBatch], int]:
    stmt = (
        select(CodeBatch)
        .options(selectinload(CodeBatch.product), selectinload(CodeBatch.sku), selectinload(CodeBatch.production_batch))
        .where(CodeBatch.tenant_id == tenant_id)
    )
    count_stmt = select(func.count()).select_from(CodeBatch).where(CodeBatch.tenant_id == tenant_id)

    if product_id:
        stmt = stmt.where(CodeBatch.product_id == product_id)
        count_stmt = count_stmt.where(CodeBatch.product_id == product_id)
    if sku_id:
        stmt = stmt.where(CodeBatch.sku_id == sku_id)
        count_stmt = count_stmt.where(CodeBatch.sku_id == sku_id)
    if status:
        stmt = stmt.where(CodeBatch.status == status)
        count_stmt = count_stmt.where(CodeBatch.status == status)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(CodeBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def list_brand_code_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeBatch], int]:
    product_ids_result = await db.execute(
        select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    product_ids = list(product_ids_result.scalars().all())

    if not product_ids:
        return [], 0

    stmt = (
        select(CodeBatch)
        .options(selectinload(CodeBatch.product), selectinload(CodeBatch.sku), selectinload(CodeBatch.production_batch))
        .where(CodeBatch.tenant_id == tenant_id, CodeBatch.product_id.in_(product_ids))
    )
    count_stmt = select(func.count()).select_from(CodeBatch).where(
        CodeBatch.tenant_id == tenant_id,
        CodeBatch.product_id.in_(product_ids),
    )

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(CodeBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def list_code_items(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_batch_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeItem], int]:
    stmt = select(CodeItem).where(CodeItem.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(CodeItem).where(CodeItem.tenant_id == tenant_id)

    if code_batch_id:
        stmt = stmt.where(CodeItem.code_batch_id == code_batch_id)
        count_stmt = count_stmt.where(CodeItem.code_batch_id == code_batch_id)
    if status:
        stmt = stmt.where(CodeItem.status == status)
        count_stmt = count_stmt.where(CodeItem.status == status)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(CodeItem.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def get_code_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> CodeBatchDetailRead | None:
    result = await db.execute(
        select(CodeBatch)
        .options(selectinload(CodeBatch.product), selectinload(CodeBatch.sku), selectinload(CodeBatch.production_batch))
        .where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    # 各状态码数量统计
    stats_result = await db.execute(
        select(CodeItem.status, func.count()).where(CodeItem.code_batch_id == batch_id).group_by(CodeItem.status)
    )
    stats = {str(status): count for status, count in stats_result.all()}

    return CodeBatchDetailRead(
        id=batch.id,
        tenant_id=batch.tenant_id,
        product_id=batch.product_id,
        sku_id=batch.sku_id,
        production_batch_id=batch.production_batch_id,
        batch_code=batch.batch_code,
        quantity=batch.quantity,
        status=batch.status,
        code_type=batch.code_type,
        generation_mode=batch.generation_mode,
        created_by=batch.created_by,
        product_name=batch.product_name,
        sku_name=batch.sku_name,
        sku_code=batch.sku_code,
        production_batch_code=batch.production_batch_code,
        production_date=batch.production_date,
        production_origin=batch.production_origin,
        created_at=batch.created_at,
        stats=stats,
    )


async def get_code_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
) -> CodeItem | None:
    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def resolve_code_by_public_id(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
) -> dict | None:
    result = await db.execute(
        select(CodeItem).where(
            CodeItem.public_id == public_id,
            CodeItem.tenant_id == tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        return None

    # 获取关联的批次信息以提取 product_id / sku_id
    batch_result = await db.execute(select(CodeBatch).where(CodeBatch.id == item.code_batch_id))
    batch = batch_result.scalar_one_or_none()

    return {
        "id": str(item.id),
        "public_id": item.public_id,
        "status": item.status,
        "code_batch_id": str(item.code_batch_id),
        "product_id": str(batch.product_id) if batch else None,
        "sku_id": str(batch.sku_id) if batch else None,
    }


async def activate_batch(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> CodeBatchActivateResponse:
    from sqlalchemy import update as sa_update

    from app.services.code_state import InvalidStateTransitionError, can_transition

    batch = await db.get(CodeBatch, batch_id)
    if not batch or batch.tenant_id != tenant_id:
        raise ValueError("Code batch not found")
    if batch.status == CodeBatchStatus.activated:
        raise InvalidStateTransitionError("Code batch is already activated")
    if batch.status != CodeBatchStatus.completed:
        raise InvalidStateTransitionError(f"Cannot activate code batch with status '{batch.status.value}'")

    # Validate current state with a lightweight count query
    result = await db.execute(
        select(CodeItem.status)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.created,
        )
        .limit(1)
    )
    sample = result.scalar_one_or_none()
    if sample is not None:
        can_transition(sample, CodeItemStatus.activated, raise_on_invalid=True)

    now = utcnow()
    stmt = (
        sa_update(CodeItem)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.created,
        )
        .values(status=CodeItemStatus.activated, activated_at=now)
    )
    r = await db.execute(stmt)
    if r.rowcount == 0:
        raise InvalidStateTransitionError("No generated codes can be activated")
    batch.status = CodeBatchStatus.activated
    await db.flush()
    return CodeBatchActivateResponse(activated=r.rowcount)


async def revoke_code_item(db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID) -> CodeItem:

    from app.services.code_state import can_transition

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")
    can_transition(item.status, CodeItemStatus.revoked, raise_on_invalid=True)
    item.status = CodeItemStatus.revoked
    item.revoked_at = utcnow()
    await db.flush()
    # 清除解析缓存，确保下次扫码立即看到 revoked 状态
    from app.services.resolve_cache import resolve_cache

    await resolve_cache.invalidate(f"resolve:{item.public_id}")
    await db.refresh(item)
    return item


async def bind_code_item(db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID) -> CodeItem:

    from app.services.code_state import can_transition

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")
    can_transition(item.status, CodeItemStatus.bound, raise_on_invalid=True)
    item.status = CodeItemStatus.bound
    item.bound_at = utcnow()
    await db.flush()
    await db.refresh(item)
    return item


async def freeze_batch(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> CodeBatchFreezeResponse:
    from sqlalchemy import update as sa_update

    from app.services.code_state import InvalidStateTransitionError, can_transition

    # Validate current state before bulk update
    sample_result = await db.execute(
        select(CodeItem.status)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status.in_([CodeItemStatus.activated, CodeItemStatus.bound]),
        )
        .limit(1)
    )
    sample = sample_result.scalar_one_or_none()
    if sample is not None:
        try:
            can_transition(sample, CodeItemStatus.frozen, raise_on_invalid=True)
        except Exception as e:
            raise InvalidStateTransitionError(str(e)) from e

    stmt = (
        sa_update(CodeItem)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status.in_([CodeItemStatus.activated, CodeItemStatus.bound]),
        )
        .values(status=CodeItemStatus.frozen)
    )
    r = await db.execute(stmt)
    await db.flush()
    # 批量清除被冻结码的解析缓存
    from app.services.resolve_cache import resolve_cache

    affected = await db.execute(
        select(CodeItem.public_id).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.frozen,
        )
    )
    for (pid,) in affected.all():
        await resolve_cache.invalidate(f"resolve:{pid}")
    return CodeBatchFreezeResponse(frozen=r.rowcount)


async def void_batch(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> CodeBatchVoidResponse:
    from sqlalchemy import update as sa_update

    now = utcnow()
    stmt = (
        sa_update(CodeItem)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status != CodeItemStatus.revoked,
        )
        .values(status=CodeItemStatus.revoked, revoked_at=now)
    )
    r = await db.execute(stmt)
    await db.flush()
    # 批量清除被作废码的解析缓存
    from app.services.resolve_cache import resolve_cache

    affected = await db.execute(
        select(CodeItem.public_id).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.revoked,
        )
    )
    for (pid,) in affected.all():
        await resolve_cache.invalidate(f"resolve:{pid}")
    return CodeBatchVoidResponse(voided=r.rowcount)


async def mark_printing(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    """标记码批次为印刷中（completed -> printing）"""
    from app.services.batch_state import can_transition_batch

    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise ValueError("Code batch not found")
    can_transition_batch(batch.status, CodeBatchStatus.printing, raise_on_invalid=True)

    batch.status = CodeBatchStatus.printing
    await db.flush()
    detail = await get_code_batch(db, tenant_id, batch_id)
    if detail is None:
        raise ValueError("Code batch not found after update")
    return detail


async def mark_delivered(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    """标记码批次为已交付（printing -> delivered）"""
    from app.services.batch_state import can_transition_batch

    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise ValueError("Code batch not found")
    can_transition_batch(batch.status, CodeBatchStatus.delivered, raise_on_invalid=True)

    batch.status = CodeBatchStatus.delivered
    await db.flush()
    detail = await get_code_batch(db, tenant_id, batch_id)
    if detail is None:
        raise ValueError("Code batch not found after update")
    return detail


_BATCH_ALLOWED_FIELDS = {"batch_code"}


async def update_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    **kwargs,
) -> CodeBatchDetailRead | None:
    batch = await get_code_batch(db, tenant_id, batch_id)
    if not batch:
        return None
    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    for k, v in kwargs.items():
        if k in _BATCH_ALLOWED_FIELDS and v is not None:
            setattr(obj, k, v)
    await db.flush()
    return await get_code_batch(db, tenant_id, batch_id)


_ITEM_ALLOWED_FIELDS = set()


async def update_code_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    **kwargs,
) -> CodeItem | None:
    from app.core.exceptions import BadRequestError

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        return None
    if "status" in kwargs:
        raise BadRequestError(
            "Direct status modification is not allowed. "
            "Use dedicated endpoints: /bind, /revoke, /activate."
        )
    for k, v in kwargs.items():
        if k in _ITEM_ALLOWED_FIELDS and v is not None:
            setattr(item, k, v)
    await db.flush()
    await db.refresh(item)
    return item
