"""积分商城服务：积分商品 CRUD、兑换逻辑。"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.member import (
    PointProduct,
)
from app.services.member import spend_points

logger = logging.getLogger(__name__)


async def list_point_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    enabled_only: bool = False,
) -> tuple[list[PointProduct], int]:
    """查询积分商品列表。"""
    stmt = select(PointProduct).where(PointProduct.tenant_id == tenant_id)
    if enabled_only:
        stmt = stmt.where(PointProduct.enabled.is_(True), PointProduct.stock > 0)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(PointProduct.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def get_point_product(
    db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID
) -> PointProduct | None:
    result = await db.execute(
        select(PointProduct).where(
            PointProduct.id == product_id, PointProduct.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()


async def create_point_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    name: str,
    description: str | None = None,
    image_url: str | None = None,
    points_cost: int,
    stock: int = 0,
    benefit_id: uuid.UUID | None = None,
) -> PointProduct:
    product = PointProduct(
        tenant_id=tenant_id,
        name=name,
        description=description,
        image_url=image_url,
        points_cost=points_cost,
        stock=stock,
        benefit_id=benefit_id,
    )
    db.add(product)
    await db.flush()
    await db.refresh(product)
    return product


async def update_point_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    **kwargs,
) -> PointProduct | None:
    product = await get_point_product(db, tenant_id, product_id)
    if not product:
        return None
    for key, value in kwargs.items():
        if hasattr(product, key) and value is not None:
            setattr(product, key, value)
    await db.flush()
    await db.refresh(product)
    return product


async def delete_point_product(
    db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID
) -> bool:
    product = await get_point_product(db, tenant_id, product_id)
    if not product:
        return False
    await db.delete(product)
    await db.flush()
    return True


async def exchange_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    product_id: uuid.UUID,
) -> dict:
    """消费者兑换积分商品。"""
    # 使用 FOR UPDATE 行锁防止并发超卖
    result = await db.execute(
        select(PointProduct)
        .where(PointProduct.id == product_id, PointProduct.tenant_id == tenant_id)
        .with_for_update()
    )
    product = result.scalar_one_or_none()
    if not product:
        raise ValueError("商品不存在")
    if not product.enabled:
        raise ValueError("商品已下架")
    if product.stock <= 0:
        raise ValueError("库存不足")

    # 消费积分
    txn = await spend_points(
        db,
        tenant_id,
        consumer_id,
        product.points_cost,
        f"兑换: {product.name}",
        reference_id=f"product:{product_id}",
    )

    # 扣减库存（行锁保护下安全操作）
    product.stock -= 1
    product.total_claimed += 1
    await db.flush()

    # 如果关联了权益，触发权益领取
    claim_id = None
    if product.benefit_id:
        try:
            from app.services.campaign import claim_benefit

            result = await claim_benefit(
                db, tenant_id, product.benefit_id, str(consumer_id),
                idempotency_key=f"points_exchange:{product_id}:{consumer_id}",
            )
            if result.get("status") == "success" and result.get("claim"):
                claim_id = result["claim"].get("id")
        except Exception:
            logger.warning(
                "Benefit claim failed for product %s", product_id, exc_info=True
            )

    return {
        "transaction_id": str(txn.id),
        "product_name": product.name,
        "points_spent": product.points_cost,
        "balance_after": txn.balance_after,
        "claim_id": claim_id,
    }
