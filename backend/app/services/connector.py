"""外部权益连接器服务"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.campaign import BenefitClaim
from app.models.connector import Connector, CouponCode, CouponPool


async def create_coupon_pool(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    codes: list[str],
) -> CouponPool:
    pool = CouponPool(
        tenant_id=tenant_id,
        name=name,
        total_codes=len(codes),
        remaining=len(codes),
    )
    db.add(pool)
    await db.flush()

    for code in codes:
        cc = CouponCode(tenant_id=tenant_id, pool_id=pool.id, code=code)
        db.add(cc)
    await db.flush()
    await db.refresh(pool)
    return pool


async def distribute_coupon(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pool_id: uuid.UUID,
    consumer_id: str,
    *,
    claim_id: uuid.UUID | None = None,
) -> CouponCode | None:
    if db.get_bind().dialect.name == "postgresql":
        try:
            result = await db.execute(
                text("SELECT * FROM public.allocate_coupon_code(:tenant_id,:pool_id,:consumer_id,:claim_id)"),
                {
                    "tenant_id": tenant_id,
                    "pool_id": pool_id,
                    "consumer_id": consumer_id,
                    "claim_id": claim_id,
                },
            )
        except DBAPIError as exc:
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == "23503":
                raise NotFoundError("Coupon pool not found") from exc
            if sqlstate == "23514":
                return None
            if sqlstate in {"42501", "22023", "55P03"}:
                raise ConflictError("Coupon allocation conflict") from exc
            raise
        row = result.mappings().one()
        code_result = await db.execute(
            select(CouponCode).where(
                CouponCode.id == row["coupon_code_id"],
                CouponCode.tenant_id == tenant_id,
                CouponCode.pool_id == pool_id,
            )
        )
        return code_result.scalar_one()

    pool_result = await db.execute(
        select(CouponPool).where(CouponPool.id == pool_id, CouponPool.tenant_id == tenant_id).with_for_update()
    )
    pool = pool_result.scalar_one_or_none()
    if pool is None:
        raise NotFoundError("Coupon pool not found")

    if claim_id is not None:
        existing_result = await db.execute(
            select(CouponCode).where(CouponCode.tenant_id == tenant_id, CouponCode.claim_id == claim_id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing
        claim_result = await db.execute(
            select(BenefitClaim).where(
                BenefitClaim.id == claim_id,
                BenefitClaim.tenant_id == tenant_id,
                BenefitClaim.consumer_id == consumer_id,
            )
        )
        if claim_result.scalar_one_or_none() is None:
            raise NotFoundError("Benefit claim not found")

    result = await db.execute(
        select(CouponCode)
        .where(
            CouponCode.tenant_id == tenant_id,
            CouponCode.pool_id == pool_id,
            CouponCode.distributed.is_(False),
        )
        .order_by(CouponCode.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    code = result.scalar_one_or_none()
    if not code:
        return None

    code.consumer_id = consumer_id
    code.claim_id = claim_id
    code.distributed = True

    remaining_result = await db.execute(
        select(func.count())
        .select_from(CouponCode)
        .where(
            CouponCode.tenant_id == tenant_id,
            CouponCode.pool_id == pool_id,
            CouponCode.distributed.is_(False),
            CouponCode.id != code.id,
        )
    )
    pool.remaining = remaining_result.scalar_one()

    await db.flush()
    await db.refresh(code)
    return code


async def list_coupon_pools(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CouponPool], int]:
    total_result = await db.execute(
        select(func.count()).select_from(CouponPool).where(CouponPool.tenant_id == tenant_id)
    )
    result = await db.execute(
        select(CouponPool)
        .where(CouponPool.tenant_id == tenant_id)
        .order_by(CouponPool.created_at.desc(), CouponPool.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total_result.scalar_one()


async def list_pool_codes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pool_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[CouponCode], int]:
    pool_result = await db.execute(
        select(CouponPool.id).where(CouponPool.id == pool_id, CouponPool.tenant_id == tenant_id)
    )
    if pool_result.scalar_one_or_none() is None:
        raise NotFoundError("Coupon pool not found")

    count_result = await db.execute(
        select(func.count())
        .select_from(CouponCode)
        .where(CouponCode.tenant_id == tenant_id, CouponCode.pool_id == pool_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(CouponCode)
        .where(CouponCode.tenant_id == tenant_id, CouponCode.pool_id == pool_id)
        .order_by(CouponCode.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def create_connector(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    connector_type: str,
    config: dict,
) -> Connector:
    conn = Connector(
        tenant_id=tenant_id,
        name=name,
        connector_type=connector_type,
        config=config,
    )
    db.add(conn)
    await db.flush()
    await db.refresh(conn)
    return conn


async def list_connectors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    connector_type: str | None = None,
) -> tuple[list[Connector], int]:
    filters = [Connector.tenant_id == tenant_id]
    if connector_type is not None:
        filters.append(Connector.connector_type == connector_type)
    total_result = await db.execute(select(func.count()).select_from(Connector).where(*filters))
    result = await db.execute(
        select(Connector)
        .where(*filters)
        .order_by(Connector.created_at.desc(), Connector.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total_result.scalar_one()


async def get_connector(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    conn_id: uuid.UUID,
) -> Connector | None:
    result = await db.execute(select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def update_connector(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    conn_id: uuid.UUID,
    **updates,
) -> Connector:
    conn = await get_connector(db, tenant_id, conn_id)
    if not conn:
        raise ValueError("Connector not found")
    for k, v in updates.items():
        setattr(conn, k, v)
    await db.flush()
    await db.refresh(conn)
    return conn


async def test_connection(connector: Connector) -> dict:
    """测试连接器连通性（模拟）"""
    return {"success": True, "message": "连接正常", "connector_type": connector.connector_type}
