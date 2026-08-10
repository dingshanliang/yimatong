"""码管理服务层"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
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
from app.services.product import is_production_batch_effectively_active
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
    batch_code: str | None = None,
) -> dict:
    # 套餐与累计配额必须在租户行锁内检查；当前事务随后完成码写入，
    # 保证同租户并发生成不会同时越过 max_codes。
    from app.models.tenant import Tenant
    from app.services.quota import check_quota, check_quota_incremental_locked

    if generation_mode == CodeGenerationMode.batch_level:
        generated_code_count = 1
    elif generation_mode == CodeGenerationMode.item_level:
        generated_code_count = quantity * (2 if code_type == CodeType.paired else 1)
    else:
        raise ValueError("Invalid generation mode")

    product = await db.get(Product, product_id)
    if not product or product.tenant_id != tenant_id:
        raise ValueError("Product not found")

    sku = await db.get(SKU, sku_id)
    if not sku or sku.tenant_id != tenant_id:
        raise ValueError("SKU not found")
    if sku.product_id != product_id:
        raise ValueError("SKU does not belong to selected product")

    production_batch_result = await db.execute(
        select(ProductionBatch)
        .where(ProductionBatch.id == production_batch_id, ProductionBatch.tenant_id == tenant_id)
        .with_for_update()
    )
    production_batch = production_batch_result.scalar_one_or_none()
    if not production_batch:
        raise ValueError("Production batch not found")
    if production_batch.product_id != product_id or production_batch.sku_id != sku_id:
        raise ValueError("Production batch does not belong to selected product and SKU")
    if not is_production_batch_effectively_active(production_batch):
        raise ConflictError("Production batch is not active")

    # Validate all referenced resources before reserving. A service caller may
    # deliberately catch a validation error and keep using its transaction;
    # invalid input must therefore never leave a quota reservation behind.
    await check_quota_incremental_locked(
        db,
        tenant_id,
        "max_codes",
        CodeItem,
        generated_code_count,
    )
    tenant = await db.get(Tenant, tenant_id)
    if tenant and tenant.quota:
        check_quota(tenant.quota, "max_codes_per_batch", generated_code_count)

    if generation_mode == CodeGenerationMode.batch_level:
        quantity = 1
        code_type = CodeType.single

    requested_batch_code = batch_code or production_batch.batch_code
    existing_batch_codes_result = await db.execute(
        select(CodeBatch.batch_code).where(
            CodeBatch.tenant_id == tenant_id,
            CodeBatch.batch_code.like(f"{requested_batch_code}%"),
        )
    )
    existing_batch_codes = set(existing_batch_codes_result.scalars().all())
    resolved_batch_code = requested_batch_code
    suffix = 2
    while resolved_batch_code in existing_batch_codes:
        suffix_text = f"-{suffix}"
        resolved_batch_code = f"{requested_batch_code[: 100 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        production_batch_id=production_batch_id,
        batch_code=resolved_batch_code,
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

    await _audit_code_op(
        db,
        str(created_by),
        str(tenant_id),
        "code_batch_created",
        f"code_batch:{batch.id}",
    )

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
    count_stmt = (
        select(func.count())
        .select_from(CodeBatch)
        .where(
            CodeBatch.tenant_id == tenant_id,
            CodeBatch.product_id.in_(product_ids),
        )
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


async def activate_batch(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID, actor_id: str | None = None
) -> CodeBatchActivateResponse:
    from sqlalchemy import update as sa_update

    from app.services.code_state import InvalidStateTransitionError, can_transition

    # Locate the authoritative parent without taking a child lock. The write
    # transaction then follows the global PB -> CodeBatch -> CodeItem order.
    production_batch_id = await db.scalar(
        select(CodeBatch.production_batch_id).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    if production_batch_id is None:
        raise ValueError("Code batch not found")

    production_batch = await db.scalar(
        select(ProductionBatch)
        .where(
            ProductionBatch.id == production_batch_id,
            ProductionBatch.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if production_batch is None:
        raise InvalidStateTransitionError("Production batch is not active")

    result = await db.execute(
        select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id).with_for_update()
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise ValueError("Code batch not found")
    if (
        batch.production_batch_id != production_batch.id
        or batch.product_id != production_batch.product_id
        or batch.sku_id != production_batch.sku_id
    ):
        raise InvalidStateTransitionError("Code batch production batch association changed")
    if batch.status == CodeBatchStatus.activated:
        raise InvalidStateTransitionError("Code batch is already activated")
    if batch.status != CodeBatchStatus.completed:
        raise InvalidStateTransitionError(f"Cannot activate code batch with status '{batch.status.value}'")
    if not is_production_batch_effectively_active(production_batch):
        raise InvalidStateTransitionError("Production batch is not active")

    # Validate current state with a lightweight count query
    result = await db.execute(
        select(CodeItem.status)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.created,
        )
        .limit(1)
        .with_for_update()
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
    # 状态变更审计（yimatong-zgb1.3 AC5）
    await _audit_code_op(
        db,
        actor_id or str(batch.created_by),
        str(tenant_id),
        "code_activate",
        f"code_batch:{batch_id}",
    )
    return CodeBatchActivateResponse(activated=r.rowcount)


async def _invalidate_resolve_cache(public_id: str) -> None:
    """清除单个码的解析缓存"""
    from app.services.resolve_cache import resolve_cache

    await resolve_cache.invalidate(f"resolve:{public_id}")


async def _invalidate_batch_cache(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    status: str,
) -> None:
    """批量清除码批次中指定状态的解析缓存"""
    from app.services.resolve_cache import resolve_cache

    affected = await db.execute(
        select(CodeItem.public_id).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == status,
        )
    )
    for (pid,) in affected.all():
        await resolve_cache.invalidate(f"resolve:{pid}")


async def revoke_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID, actor_id: str | None = None
) -> CodeItem:
    from app.services.code_state import can_transition

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundError("Code item not found")
    can_transition(item.status, CodeItemStatus.revoked, raise_on_invalid=True)
    item.status = CodeItemStatus.revoked
    item.revoked_at = utcnow()
    await db.flush()
    # 清除解析缓存，确保下次扫码立即看到 revoked 状态
    await _invalidate_resolve_cache(item.public_id)
    # 状态变更审计（yimatong-zgb1.3 AC5）
    await _audit_code_op(db, actor_id, str(tenant_id), "code_revoke", f"code_item:{item.public_id}")
    await db.refresh(item)
    return item


async def bind_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID, actor_id: str | None = None
) -> CodeItem:
    from app.services.code_state import InvalidStateTransitionError, can_transition

    locator_result = await db.execute(
        select(CodeItem.code_batch_id, CodeBatch.production_batch_id)
        .join(
            CodeBatch,
            (CodeBatch.id == CodeItem.code_batch_id) & (CodeBatch.tenant_id == CodeItem.tenant_id),
        )
        .where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    locator = locator_result.one_or_none()
    if locator is None:
        raise NotFoundError("Code item not found")

    code_batch_id, production_batch_id = locator
    production_batch = await db.scalar(
        select(ProductionBatch)
        .where(
            ProductionBatch.id == production_batch_id,
            ProductionBatch.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if production_batch is None:
        raise ConflictError("Code item production chain is unavailable", error_code="CODE_BIND_CHAIN_CONFLICT")

    batch = await db.scalar(
        select(CodeBatch).where(CodeBatch.id == code_batch_id, CodeBatch.tenant_id == tenant_id).with_for_update()
    )
    if batch is None:
        raise ConflictError("Code item production chain is unavailable", error_code="CODE_BIND_CHAIN_CONFLICT")
    if (
        batch.production_batch_id != production_batch.id
        or batch.product_id != production_batch.product_id
        or batch.sku_id != production_batch.sku_id
    ):
        raise ConflictError("Code item production chain changed", error_code="CODE_BIND_CHAIN_CONFLICT")
    if batch.status != CodeBatchStatus.activated:
        raise ConflictError("Code batch is not active", error_code="CODE_BIND_BATCH_NOT_ACTIVE")
    if not is_production_batch_effectively_active(production_batch):
        raise ConflictError("Production batch is not active", error_code="PRODUCTION_BATCH_NOT_ACTIVE")

    item = await db.scalar(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id).with_for_update()
    )
    if item is None:
        raise NotFoundError("Code item not found")
    if item.code_batch_id != batch.id:
        raise ConflictError("Code item production chain changed", error_code="CODE_BIND_CHAIN_CONFLICT")
    try:
        can_transition(item.status, CodeItemStatus.bound, raise_on_invalid=True)
    except InvalidStateTransitionError as exc:
        raise ConflictError(
            "Code item cannot be bound in its current lifecycle state",
            error_code="CODE_BIND_CONFLICT",
        ) from exc
    item.status = CodeItemStatus.bound
    item.bound_at = utcnow()
    await db.flush()
    await _audit_code_op(db, actor_id, str(tenant_id), "code_bind", f"code_item:{item.public_id}")
    await db.refresh(item)
    return item


async def freeze_batch(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID, actor_id: str | None = None
) -> CodeBatchFreezeResponse:
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
    await _invalidate_batch_cache(db, tenant_id, batch_id, CodeItemStatus.frozen)
    # 状态变更审计（yimatong-zgb1.3 AC5）
    await _audit_code_op(db, actor_id, str(tenant_id), "code_freeze", f"code_batch:{batch_id}")
    return CodeBatchFreezeResponse(frozen=r.rowcount)


async def void_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    actor_id: str | None = None,
    reason: str | None = None,
) -> CodeBatchVoidResponse:
    """作废整批码（不可逆，yimatong-zgb1.3 强制生命周期合约）。

    权威四状态规则：任何未作废（voided）的码都可作废（unactivated/active/frozen → voided）；
    已经作废（revoked/expired）的幂等跳过。voided 是终态，本操作之后该码不能再回到其他状态。

    yimatong-zgb1.8 AC2+AC3：作废是受保护的不可逆动作，必须记录原因。
    reason 写入审计日志的 resource 详情（User Story 28）。
    """
    from sqlalchemy import update as sa_update

    from app.models.code import CodeLifecycle, to_lifecycle
    from app.services.code_lifecycle import can_lifecycle_transition

    # 先取该批所有码的当前状态，校验可作废（已作废的幂等跳过；其余必须能转到 voided）
    result = await db.execute(
        select(CodeItem.id, CodeItem.status).where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch_id)
    )
    voidable_ids: list[uuid.UUID] = []
    for item_id, status in result.all():
        # 已作废（映射到 voided）幂等跳过
        if to_lifecycle(status) == CodeLifecycle.voided:
            continue
        # 校验可作废（unactivated/active/frozen → voided 均合法）
        can_lifecycle_transition(status, CodeLifecycle.voided, raise_on_invalid=True)
        voidable_ids.append(item_id)

    voided_count = 0
    if voidable_ids:
        now = utcnow()
        stmt = (
            sa_update(CodeItem)
            .where(
                CodeItem.tenant_id == tenant_id,
                CodeItem.id.in_(voidable_ids),
            )
            .values(status=CodeItemStatus.revoked, revoked_at=now)
        )
        r = await db.execute(stmt)
        voided_count = r.rowcount or 0
        await db.flush()
        # 批量清除被作废码的解析缓存
        await _invalidate_batch_cache(db, tenant_id, batch_id, CodeItemStatus.revoked)
    # 状态变更审计（yimatong-zgb1.3 AC5 + 1.8 AC3 reason）— 即使 voided_count=0（幂等）也记录尝试
    # yimatong-zgb1.8：resource 包含 reason，便于审计追溯
    resource = f"code_batch:{batch_id}"
    if reason:
        resource += f" reason:{reason[:200]}"
    await _audit_code_op(db, actor_id, str(tenant_id), "code_void", resource)
    return CodeBatchVoidResponse(voided=voided_count)


async def _audit_code_op(
    db: AsyncSession,
    actor_id: str | None,
    target_tenant_id: str,
    action: str,
    resource: str,
) -> None:
    """Write a mandatory actor-bound audit record in the mutation transaction."""
    if not actor_id:
        raise ValueError("actor_id is required for code mutation audit")
    from app.services.audit import write_audit_log

    await write_audit_log(
        db,
        operator_id=actor_id,
        target_tenant_id=target_tenant_id,
        action=action,
        resource=resource,
    )


async def _lock_forward_operational_code_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> CodeBatch:
    production_batch_id = await db.scalar(
        select(CodeBatch.production_batch_id).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    if production_batch_id is None:
        raise ValueError("Code batch not found")

    production_batch = await db.scalar(
        select(ProductionBatch)
        .where(
            ProductionBatch.id == production_batch_id,
            ProductionBatch.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    batch = await db.scalar(
        select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id).with_for_update()
    )
    if production_batch is None or batch is None:
        raise ConflictError(
            "Code batch production chain is unavailable",
            error_code="CODE_BATCH_PRODUCTION_CHAIN_CONFLICT",
        )
    if (
        batch.production_batch_id != production_batch.id
        or batch.product_id != production_batch.product_id
        or batch.sku_id != production_batch.sku_id
    ):
        raise ConflictError(
            "Code batch production chain changed",
            error_code="CODE_BATCH_PRODUCTION_CHAIN_CONFLICT",
        )
    if not is_production_batch_effectively_active(production_batch):
        raise ConflictError(
            "Production batch is not active",
            error_code="PRODUCTION_BATCH_NOT_ACTIVE",
        )
    return batch


async def mark_printing(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID, actor_id: str | None = None
) -> dict:
    """标记码批次为印刷中（completed -> printing）"""
    from app.services.batch_state import can_transition_batch

    batch = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    can_transition_batch(batch.status, CodeBatchStatus.printing, raise_on_invalid=True)

    batch.status = CodeBatchStatus.printing
    await db.flush()
    await _audit_code_op(db, actor_id, str(tenant_id), "code_mark_printing", f"code_batch:{batch_id}")
    detail = await get_code_batch(db, tenant_id, batch_id)
    if detail is None:
        raise ValueError("Code batch not found after update")
    return detail


async def mark_delivered(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID, actor_id: str | None = None
) -> dict:
    """标记码批次为已交付（printing -> delivered）"""
    from app.services.batch_state import can_transition_batch

    batch = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    can_transition_batch(batch.status, CodeBatchStatus.delivered, raise_on_invalid=True)

    batch.status = CodeBatchStatus.delivered
    await db.flush()
    await _audit_code_op(db, actor_id, str(tenant_id), "code_mark_delivered", f"code_batch:{batch_id}")
    detail = await get_code_batch(db, tenant_id, batch_id)
    if detail is None:
        raise ValueError("Code batch not found after update")
    return detail


_BATCH_ALLOWED_FIELDS = {"batch_code"}


async def update_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    actor_id: str | None = None,
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
    await _audit_code_op(db, actor_id, str(tenant_id), "code_batch_updated", f"code_batch:{batch_id}")
    return await get_code_batch(db, tenant_id, batch_id)


_ITEM_ALLOWED_FIELDS = set()


async def update_code_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    **kwargs,
) -> CodeItem | None:

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        return None
    if "status" in kwargs:
        raise BadRequestError(
            "Direct status modification is not allowed. Use dedicated endpoints: /bind, /revoke, /activate."
        )
    for k, v in kwargs.items():
        if k in _ITEM_ALLOWED_FIELDS and v is not None:
            setattr(item, k, v)
    await db.flush()
    await db.refresh(item)
    return item
