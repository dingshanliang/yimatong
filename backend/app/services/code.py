"""码管理服务层"""

import hashlib
import hmac
import json
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.code import (
    CodeBatch,
    CodeBatchGenerationReceipt,
    CodeBatchSource,
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
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils import utcnow

CODE_OPERATION_RATE_LIMIT_MAX_ATTEMPTS = 10
CODE_OPERATION_RATE_LIMIT_WINDOW_SECONDS = 60

_code_operation_rate_cache = AsyncRedisCache(
    prefix="code_operation_security",
    default_ttl=CODE_OPERATION_RATE_LIMIT_WINDOW_SECONDS,
)

_CODE_BATCH_DB_CONFLICTS = {
    "22023": ("Code batch request is invalid", "CODE_BATCH_REQUEST_INVALID"),
    "23514": ("Code batch contract or state is invalid", "CODE_BATCH_CONTRACT_CONFLICT"),
    "55P03": ("Code batch is busy; retry the request", "CODE_BATCH_BUSY"),
    "55000": ("Code batch is immutable in its current state", "CODE_BATCH_IMMUTABLE"),
}


def map_code_batch_db_error(exc: DBAPIError) -> ConflictError | None:
    """Map known PostgreSQL contract failures without exposing DB details."""

    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "23505":
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_code_batches_tenant_batch_code":
            return ConflictError("Code batch number is already in use", error_code="CODE_BATCH_CODE_CONFLICT")
        return None
    response = _CODE_BATCH_DB_CONFLICTS.get(sqlstate)
    if response is None:
        return None
    detail, error_code = response
    return ConflictError(detail, error_code=error_code)


def _code_contract_hmac(domain: str, value: str) -> str:
    try:
        secret = bytes.fromhex(settings.hmac_pepper)
    except ValueError as exc:
        raise RuntimeError("HMAC pepper is not valid hexadecimal") from exc
    return hmac.new(secret, f"code-batch:{domain}:v1:{value}".encode(), hashlib.sha256).hexdigest()


def _generation_idempotency_material(
    *,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    idempotency_key: str,
    payload: dict,
) -> tuple[str, str]:
    try:
        parsed_key = uuid.UUID(idempotency_key)
    except (ValueError, AttributeError) as exc:
        raise BadRequestError("Idempotency-Key must be a canonical UUID") from exc
    if str(parsed_key) != idempotency_key:
        raise BadRequestError("Idempotency-Key must be a canonical UUID")
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = _code_contract_hmac("idempotency", f"{tenant_id}:{created_by}:{idempotency_key}")
    fingerprint = _code_contract_hmac("request", canonical_payload)
    return digest, fingerprint


async def _claim_generation_receipt(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    idempotency_digest: str,
    request_fingerprint: str,
) -> tuple[CodeBatchGenerationReceipt, bool]:
    receipt = await db.scalar(
        select(CodeBatchGenerationReceipt)
        .where(
            CodeBatchGenerationReceipt.tenant_id == tenant_id,
            CodeBatchGenerationReceipt.idempotency_digest == idempotency_digest,
        )
        .with_for_update()
    )
    if receipt is not None:
        return receipt, False

    receipt = CodeBatchGenerationReceipt(
        tenant_id=tenant_id,
        created_by=created_by,
        idempotency_digest=idempotency_digest,
        request_fingerprint=request_fingerprint,
    )
    try:
        async with db.begin_nested():
            db.add(receipt)
            await db.flush()
        return receipt, True
    except IntegrityError:
        receipt = await db.scalar(
            select(CodeBatchGenerationReceipt)
            .where(
                CodeBatchGenerationReceipt.tenant_id == tenant_id,
                CodeBatchGenerationReceipt.idempotency_digest == idempotency_digest,
            )
            .with_for_update()
        )
        if receipt is None:
            raise ConflictError(
                "Code batch generation request is already in progress",
                error_code="CODE_BATCH_GENERATION_IN_PROGRESS",
            )
        return receipt, False


async def enforce_code_operation_rate_limit(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
) -> None:
    """Fail closed before expensive code generation or export work."""

    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"{tenant_id}:{account_id}".encode(), hashlib.sha256).hexdigest()
    try:
        allowed, _ = await _code_operation_rate_cache.rate_limit_check_shared(
            f"principal:{digest}",
            CODE_OPERATION_RATE_LIMIT_MAX_ATTEMPTS,
            CODE_OPERATION_RATE_LIMIT_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Code operation service is temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many code generation or export requests",
            headers={"Retry-After": str(CODE_OPERATION_RATE_LIMIT_WINDOW_SECONDS)},
        )


def _batch_creation_response(
    batch: CodeBatch,
    *,
    generated_count: int,
    product: Product,
    sku: SKU,
    production_batch: ProductionBatch,
) -> dict:
    return {
        "id": str(batch.id),
        "tenant_id": str(batch.tenant_id),
        "product_id": str(batch.product_id),
        "sku_id": str(batch.sku_id),
        "production_batch_id": str(batch.production_batch_id),
        "batch_code": batch.batch_code,
        "quantity": batch.quantity,
        "expected_item_count": batch.expected_item_count,
        "generated_count": generated_count,
        "status": batch.status,
        "code_type": batch.code_type,
        "generation_mode": batch.generation_mode,
        "source": batch.source,
        "created_by": str(batch.created_by),
        "product_name": product.name,
        "sku_name": sku.name,
        "sku_code": sku.code,
        "production_batch_code": production_batch.batch_code,
        "production_date": production_batch.production_date,
        "production_origin": production_batch.origin,
    }


async def _replay_generation_response(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> dict:
    batch = await db.scalar(
        select(CodeBatch)
        .options(
            selectinload(CodeBatch.product),
            selectinload(CodeBatch.sku),
            selectinload(CodeBatch.production_batch),
        )
        .where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    if batch is None or batch.product is None or batch.sku is None or batch.production_batch is None:
        raise ConflictError(
            "Idempotent code batch result is unavailable",
            error_code="CODE_BATCH_IDEMPOTENCY_RESULT_UNAVAILABLE",
        )
    generated_count = await db.scalar(
        select(func.count())
        .select_from(CodeItem)
        .where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch_id)
    )
    return _batch_creation_response(
        batch,
        generated_count=generated_count or 0,
        product=batch.product,
        sku=batch.sku,
        production_batch=batch.production_batch,
    )


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
    idempotency_key: str | None = None,
    source: str = CodeBatchSource.generated,
) -> dict:
    # 套餐与累计配额必须在租户行锁内检查；当前事务随后完成码写入，
    # 保证同租户并发生成不会同时越过 max_codes。
    from app.models.tenant import Tenant
    from app.services.quota import check_quota, check_quota_incremental_locked

    try:
        resolved_code_type = CodeType(code_type)
        resolved_generation_mode = CodeGenerationMode(generation_mode)
        resolved_source = CodeBatchSource(source)
    except ValueError as exc:
        raise BadRequestError("Invalid code batch type or generation mode") from exc
    if resolved_code_type not in {CodeType.single, CodeType.paired}:
        raise BadRequestError("Code batch type must be single or paired")
    if quantity < 1 or quantity > 10_000:
        raise BadRequestError("Code batch quantity must be between 1 and 10,000")
    if resolved_code_type == CodeType.paired and quantity > 5_000:
        raise BadRequestError("Paired code batch quantity cannot exceed 5,000 pairs")
    if resolved_source == CodeBatchSource.imported and (
        resolved_code_type != CodeType.single or resolved_generation_mode != CodeGenerationMode.item_level
    ):
        raise BadRequestError("Imported code batches must use item-level single codes")

    code_type = resolved_code_type
    generation_mode = resolved_generation_mode
    source = resolved_source
    receipt: CodeBatchGenerationReceipt | None = None
    if idempotency_key is not None:
        idempotency_digest, request_fingerprint = _generation_idempotency_material(
            tenant_id=tenant_id,
            created_by=created_by,
            idempotency_key=idempotency_key,
            payload={
                "batch_code": batch_code,
                "code_type": code_type.value,
                "generation_mode": generation_mode.value,
                "product_id": str(product_id),
                "production_batch_id": str(production_batch_id),
                "quantity": quantity,
                "sku_id": str(sku_id),
                "source": source.value,
            },
        )
        receipt, claimed = await _claim_generation_receipt(
            db,
            tenant_id=tenant_id,
            created_by=created_by,
            idempotency_digest=idempotency_digest,
            request_fingerprint=request_fingerprint,
        )
        if not hmac.compare_digest(receipt.request_fingerprint, request_fingerprint):
            raise ConflictError(
                "Idempotency-Key was already used with a different request",
                error_code="CODE_BATCH_IDEMPOTENCY_CONFLICT",
            )
        if not claimed:
            if receipt.code_batch_id is None:
                raise ConflictError(
                    "Code batch generation request is already in progress",
                    error_code="CODE_BATCH_GENERATION_IN_PROGRESS",
                )
            return await _replay_generation_response(db, tenant_id, receipt.code_batch_id)

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
        contract_version=1,
        source=source,
        expected_item_count=generated_code_count,
        status=CodeBatchStatus.generating,
    )
    db.add(batch)
    await db.flush()
    if receipt is not None:
        receipt.code_batch_id = batch.id
        await db.flush()

    if source == CodeBatchSource.imported:
        await _audit_code_op(
            db,
            str(created_by),
            str(tenant_id),
            "code_batch_import_staged",
            f"code_batch:{batch.id}",
        )
        return _batch_creation_response(
            batch,
            generated_count=0,
            product=product,
            sku=sku,
            production_batch=production_batch,
        )

    total_generated = 0

    def _build_items(group_count: int) -> list[CodeItem]:
        used_ids: set[str] = set()

        def _unique_public_id() -> str:
            for _ in range(10):
                pid = generate_public_id()
                if pid not in used_ids:
                    used_ids.add(pid)
                    return pid
            raise ConflictError(
                "Unable to allocate unique packaging codes",
                error_code="PUBLIC_ID_ALLOCATION_EXHAUSTED",
            )

        items: list[CodeItem] = []
        if code_type == CodeType.paired:
            for _ in range(group_count):
                pair_id = uuid.uuid4()
                items.append(
                    CodeItem(
                        tenant_id=tenant_id,
                        code_batch_id=batch.id,
                        public_id=_unique_public_id(),
                        code_type=CodeType.outer,
                        pair_id=pair_id,
                    )
                )
                items.append(
                    CodeItem(
                        tenant_id=tenant_id,
                        code_batch_id=batch.id,
                        public_id=_unique_public_id(),
                        code_type=CodeType.inner,
                        pair_id=pair_id,
                    )
                )
        else:
            for _ in range(group_count):
                items.append(
                    CodeItem(
                        tenant_id=tenant_id,
                        code_batch_id=batch.id,
                        public_id=_unique_public_id(),
                        code_type=CodeType.single,
                    )
                )
        return items

    remaining_groups = quantity
    groups_per_chunk = 2_500 if code_type == CodeType.paired else 5_000
    while remaining_groups:
        group_count = min(remaining_groups, groups_per_chunk)
        for _ in range(10):
            batch_items = _build_items(group_count)
            try:
                async with db.begin_nested():
                    db.add_all(batch_items)
                    await db.flush()
                break
            except IntegrityError:
                continue
        else:
            raise ConflictError(
                "Unable to allocate unique packaging codes",
                error_code="PUBLIC_ID_ALLOCATION_EXHAUSTED",
            )
        total_generated += len(batch_items)
        remaining_groups -= group_count

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

    return _batch_creation_response(
        batch,
        generated_count=total_generated,
        product=product,
        sku=sku,
        production_batch=production_batch,
    )


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
        source=batch.source,
        expected_item_count=batch.expected_item_count,
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
    batch_result = await db.execute(
        select(CodeBatch).where(CodeBatch.id == item.code_batch_id, CodeBatch.tenant_id == tenant_id)
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
    if batch.status != CodeBatchStatus.delivered:
        raise InvalidStateTransitionError(f"Cannot activate code batch with status '{batch.status.value}'")
    if batch.export_manifest_id is None:
        raise InvalidStateTransitionError("Code batch export manifest is unavailable")
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
    details: dict | None = None,
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
        details=details,
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
    """Mark an exported code batch as printing."""
    from app.services.batch_state import can_transition_batch

    batch = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    can_transition_batch(batch.status, CodeBatchStatus.printing, raise_on_invalid=True)
    if batch.export_manifest_id is None:
        raise ConflictError(
            "Code batch export manifest is unavailable",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISSING",
        )

    batch.status = CodeBatchStatus.printing
    batch.printing_at = utcnow()
    await db.flush()
    await _audit_code_op(db, actor_id, str(tenant_id), "code_mark_printing", f"code_batch:{batch_id}")
    detail = await get_code_batch(db, tenant_id, batch_id)
    if detail is None:
        raise ValueError("Code batch not found after update")
    return detail


async def mark_delivered(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    actor_id: str | None = None,
    *,
    reason: str,
    recipient: str,
    confirm: str,
) -> dict:
    """Mark a printed batch delivered with explicit evidence."""
    from app.services.batch_state import can_transition_batch

    reason = reason.strip()
    recipient = recipient.strip()
    if not reason or len(reason) > 500:
        raise BadRequestError("Delivery reason must be between 1 and 500 characters")
    if not recipient or len(recipient) > 255:
        raise BadRequestError("Delivery recipient must be between 1 and 255 characters")
    if confirm != "deliver":
        raise BadRequestError("Delivery confirmation must be 'deliver'")
    batch = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    can_transition_batch(batch.status, CodeBatchStatus.delivered, raise_on_invalid=True)
    if batch.export_manifest_id is None:
        raise ConflictError(
            "Code batch export manifest is unavailable",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISSING",
        )

    batch.status = CodeBatchStatus.delivered
    batch.delivery_recipient = recipient
    batch.delivered_at = utcnow()
    await db.flush()
    await _audit_code_op(
        db,
        actor_id,
        str(tenant_id),
        "code_mark_delivered",
        f"code_batch:{batch_id}",
        details={
            "reason": reason,
            "recipient": recipient,
            "export_manifest_id": str(batch.export_manifest_id),
        },
    )
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
    try:
        obj = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    except ValueError:
        return None
    if obj.status in {
        CodeBatchStatus.exported,
        CodeBatchStatus.printing,
        CodeBatchStatus.delivered,
        CodeBatchStatus.activated,
    }:
        raise ConflictError("Exported code batch identity is immutable", error_code="CODE_BATCH_IMMUTABLE")
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
