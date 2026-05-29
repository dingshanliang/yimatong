"""外部权益连接器服务"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector, CouponCode, CouponPool


async def create_coupon_pool(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, codes: list[str],
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
        cc = CouponCode(pool_id=pool.id, code=code)
        db.add(cc)
    await db.flush()
    await db.refresh(pool)
    return pool


async def distribute_coupon(
    db: AsyncSession, pool_id: uuid.UUID, consumer_id: str,
) -> CouponCode | None:
    result = await db.execute(
        select(CouponCode).where(
            CouponCode.pool_id == pool_id,
            CouponCode.distributed.is_(False),
        ).order_by(CouponCode.id).limit(1)
    )
    code = result.scalar_one_or_none()
    if not code:
        return None

    code.consumer_id = consumer_id
    code.distributed = True

    pool_result = await db.execute(
        select(CouponPool).where(CouponPool.id == pool_id)
    )
    pool = pool_result.scalar_one()
    pool.remaining -= 1

    await db.flush()
    await db.refresh(code)
    return code


async def create_connector(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, connector_type: str, config: dict,
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
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[Connector]:
    result = await db.execute(
        select(Connector).where(Connector.tenant_id == tenant_id).order_by(Connector.id.desc())
    )
    return list(result.scalars().all())


async def get_connector(
    db: AsyncSession, tenant_id: uuid.UUID, conn_id: uuid.UUID,
) -> Connector | None:
    result = await db.execute(
        select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def update_connector(
    db: AsyncSession, tenant_id: uuid.UUID, conn_id: uuid.UUID, **updates,
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
