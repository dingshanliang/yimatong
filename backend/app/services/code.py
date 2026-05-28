"""码管理服务层"""

import uuid
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus, CodeType
from app.services.public_id import generate_public_id
from app.utils import utcnow


async def create_code_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    batch_code: str,
    quantity: int,
    created_by: uuid.UUID,
    code_type: str = CodeType.single,
) -> dict:
    batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        batch_code=batch_code,
        quantity=quantity,
        created_by=created_by,
        code_type=code_type,
    )
    db.add(batch)
    await db.flush()

    items = []
    BATCH_SIZE = 5000
    total_generated = 0

    def _build_items():
        if code_type == CodeType.paired:
            for _ in range(quantity):
                pair_id = uuid.uuid4()
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=generate_public_id(),
                    code_type=CodeType.outer,
                    pair_id=pair_id,
                )
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=generate_public_id(),
                    code_type=CodeType.inner,
                    pair_id=pair_id,
                )
        else:
            for _ in range(quantity):
                yield CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=batch.id,
                    public_id=generate_public_id(),
                    code_type=CodeType.single,
                )

    batch_items = []
    for item in _build_items():
        batch_items.append(item)
        if len(batch_items) >= BATCH_SIZE:
            db.add_all(batch_items)
            await db.flush()
            total_generated += len(batch_items)
            batch_items = []
    if batch_items:
        db.add_all(batch_items)
        total_generated += len(batch_items)

    batch.status = CodeBatchStatus.completed
    await db.commit()
    await db.refresh(batch)

    return {
        "id": str(batch.id),
        "tenant_id": str(batch.tenant_id),
        "product_id": str(batch.product_id),
        "sku_id": str(batch.sku_id),
        "batch_code": batch.batch_code,
        "quantity": batch.quantity,
        "generated_count": total_generated,
        "status": batch.status,
        "code_type": batch.code_type,
        "created_by": str(batch.created_by),
    }


async def list_code_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeBatch], int]:
    stmt = select(CodeBatch).where(CodeBatch.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(CodeBatch).where(CodeBatch.tenant_id == tenant_id)

    if product_id:
        stmt = stmt.where(CodeBatch.product_id == product_id)
        count_stmt = count_stmt.where(CodeBatch.product_id == product_id)
    if status:
        stmt = stmt.where(CodeBatch.status == status)
        count_stmt = count_stmt.where(CodeBatch.status == status)

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
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    # 各状态码数量统计
    stats_result = await db.execute(
        select(CodeItem.status, func.count())
        .where(CodeItem.code_batch_id == batch_id)
        .group_by(CodeItem.status)
    )
    stats = {str(status): count for status, count in stats_result.all()}

    return {
        "id": str(batch.id),
        "tenant_id": str(batch.tenant_id),
        "product_id": str(batch.product_id),
        "sku_id": str(batch.sku_id),
        "batch_code": batch.batch_code,
        "quantity": batch.quantity,
        "status": batch.status,
        "code_type": batch.code_type,
        "created_by": str(batch.created_by),
        "stats": stats,
    }


async def get_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID,
) -> CodeItem | None:
    result = await db.execute(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def resolve_code_by_public_id(
    db: AsyncSession, tenant_id: uuid.UUID, public_id: str,
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
    batch_result = await db.execute(
        select(CodeBatch).where(CodeBatch.id == item.code_batch_id)
    )
    batch = batch_result.scalar_one_or_none()

    return {
        "id": str(item.id),
        "public_id": item.public_id,
        "status": item.status,
        "code_batch_id": str(item.code_batch_id),
        "product_id": str(batch.product_id) if batch else None,
        "sku_id": str(batch.sku_id) if batch else None,
    }


async def activate_batch(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID
) -> dict:
    from sqlalchemy import update as sa_update

    from app.services.code_state import can_transition

    # Validate current state with a lightweight count query
    result = await db.execute(
        select(CodeItem.status).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.created,
        ).limit(1)
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
    await db.commit()
    return {"activated": r.rowcount}


async def revoke_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID
) -> CodeItem:
    from datetime import datetime

    from app.services.code_state import can_transition

    result = await db.execute(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")
    can_transition(item.status, CodeItemStatus.revoked, raise_on_invalid=True)
    item.status = CodeItemStatus.revoked
    item.revoked_at = utcnow()
    await db.commit()
    await db.refresh(item)
    return item


async def bind_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID
) -> CodeItem:
    from datetime import datetime

    from app.services.code_state import can_transition

    result = await db.execute(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")
    can_transition(item.status, CodeItemStatus.bound, raise_on_invalid=True)
    item.status = CodeItemStatus.bound
    item.bound_at = utcnow()
    await db.commit()
    await db.refresh(item)
    return item
