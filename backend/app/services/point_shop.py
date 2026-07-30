"""积分商城服务：积分商品 CRUD、兑换逻辑。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit
from app.models.member import (
    ConsumerProfile,
    PointProduct,
    PointRedemption,
    PointRedemptionStatus,
)
from app.services.member import spend_points
from app.utils import utcnow


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
        now = utcnow()
        stmt = stmt.where(
            PointProduct.enabled.is_(True),
            PointProduct.stock > 0,
            (PointProduct.starts_at.is_(None) | (PointProduct.starts_at <= now)),
            (PointProduct.ends_at.is_(None) | (PointProduct.ends_at >= now)),
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        stmt.order_by(PointProduct.sort_order.asc(), PointProduct.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def get_point_product(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> PointProduct | None:
    result = await db.execute(
        select(PointProduct).where(PointProduct.id == product_id, PointProduct.tenant_id == tenant_id)
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
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    per_consumer_limit: int = 1,
    sort_order: int = 0,
    enabled: bool = True,
) -> PointProduct:
    await _ensure_benefit_belongs_to_tenant(db, tenant_id, benefit_id)
    product = PointProduct(
        tenant_id=tenant_id,
        name=name,
        description=description,
        image_url=image_url,
        points_cost=points_cost,
        stock=stock,
        benefit_id=benefit_id,
        starts_at=starts_at,
        ends_at=ends_at,
        per_consumer_limit=per_consumer_limit,
        sort_order=sort_order,
        enabled=enabled,
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
    if "benefit_id" in kwargs:
        await _ensure_benefit_belongs_to_tenant(db, tenant_id, kwargs["benefit_id"])
    from app.utils.model_helpers import apply_allowed_updates

    apply_allowed_updates(
        product,
        kwargs,
        {
            "name",
            "description",
            "image_url",
            "points_cost",
            "stock",
            "benefit_id",
            "enabled",
            "starts_at",
            "ends_at",
            "per_consumer_limit",
            "sort_order",
        },
    )
    await db.flush()
    await db.refresh(product)
    return product


async def delete_point_product(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> bool:
    product = await get_point_product(db, tenant_id, product_id)
    if not product:
        return False
    # Check for existing redemptions
    count_result = await db.execute(
        select(func.count())
        .select_from(PointRedemption)
        .where(
            PointRedemption.product_id == product_id,
        )
    )
    if (count_result.scalar() or 0) > 0:
        product.enabled = False
        product.name = f"[已删除] {product.name}"
        await db.flush()
        return True
    await db.delete(product)
    await db.flush()
    return True


async def _ensure_benefit_belongs_to_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID | None,
) -> None:
    if not benefit_id:
        return
    result = await db.execute(
        select(Benefit.id).where(
            Benefit.id == benefit_id,
            Benefit.tenant_id == tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("关联权益不存在")


async def _redemption_count(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    product_id: uuid.UUID,
) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(PointRedemption)
        .where(
            PointRedemption.tenant_id == tenant_id,
            PointRedemption.consumer_id == consumer_id,
            PointRedemption.product_id == product_id,
            PointRedemption.status == PointRedemptionStatus.success,
        )
    )
    return int(result.scalar() or 0)


async def get_exchange_block_reason(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    product: PointProduct,
    current_points: int,
) -> str | None:
    count = 0
    if product.per_consumer_limit > 0:
        count = await _redemption_count(db, tenant_id, consumer_id, product.id)
    return _compute_block_reason(product, current_points, count)


def _compute_block_reason(product: PointProduct, current_points: int, redemption_count: int) -> str | None:
    """Pure function to compute exchange block reason without DB queries."""
    now = utcnow()
    if not product.enabled:
        return "商品已下架"
    if product.starts_at and product.starts_at > now:
        return "尚未开始兑换"
    if product.ends_at and product.ends_at < now:
        return "兑换已结束"
    if product.stock <= 0:
        return "库存不足"
    if current_points < product.points_cost:
        return "积分不足"
    if product.per_consumer_limit > 0 and redemption_count >= product.per_consumer_limit:
        return "已达到每人限兑次数"
    return None


async def list_consumer_point_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
) -> list[dict]:
    consumer_result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.id == consumer_id,
        )
    )
    consumer = consumer_result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")

    products, _ = await list_point_products(db, tenant_id, page=1, page_size=100, enabled_only=True)
    if not products:
        return []

    # Batch query redemption counts instead of N+1
    product_ids = [p.id for p in products]
    redemption_counts = await db.execute(
        select(PointRedemption.product_id, func.count())
        .where(
            PointRedemption.tenant_id == tenant_id,
            PointRedemption.consumer_id == consumer_id,
            PointRedemption.product_id.in_(product_ids),
            PointRedemption.status == PointRedemptionStatus.success,
        )
        .group_by(PointRedemption.product_id)
    )
    count_map = dict(redemption_counts.all())

    items = []
    for product in products:
        block_reason = _compute_block_reason(product, consumer.total_points, count_map.get(product.id, 0))
        items.append(
            serialize_point_product(product, can_exchange=block_reason is None, exchange_block_reason=block_reason)
        )
    return items


def serialize_point_product(
    product: PointProduct,
    *,
    can_exchange: bool | None = None,
    exchange_block_reason: str | None = None,
) -> dict:
    return {
        "id": str(product.id),
        "name": product.name,
        "description": product.description,
        "image_url": product.image_url,
        "points_cost": product.points_cost,
        "stock": product.stock,
        "total_claimed": product.total_claimed,
        "enabled": product.enabled,
        "benefit_id": str(product.benefit_id) if product.benefit_id else None,
        "starts_at": product.starts_at.isoformat() if product.starts_at else None,
        "ends_at": product.ends_at.isoformat() if product.ends_at else None,
        "per_consumer_limit": product.per_consumer_limit,
        "sort_order": product.sort_order,
        "can_exchange": can_exchange,
        "exchange_block_reason": exchange_block_reason,
    }


async def exchange_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    product_id: uuid.UUID,
) -> dict:
    """消费者兑换积分商品。"""
    # 使用 FOR UPDATE 行锁防止并发超卖
    result = await db.execute(
        select(PointProduct).where(PointProduct.id == product_id, PointProduct.tenant_id == tenant_id).with_for_update()
    )
    product = result.scalar_one_or_none()
    if not product:
        raise ValueError("商品不存在")
    if not product.enabled:
        raise ValueError("商品已下架")
    if product.stock <= 0:
        raise ValueError("库存不足")
    consumer_result = await db.execute(
        select(ConsumerProfile)
        .where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    consumer = consumer_result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")
    block_reason = await get_exchange_block_reason(db, tenant_id, consumer_id, product, consumer.total_points)
    if block_reason:
        raise ValueError(block_reason)

    # 消费积分
    txn = await spend_points(
        db,
        tenant_id,
        consumer_id,
        product.points_cost,
        f"兑换: {product.name[:196]}",
        reference_id=f"product:{product_id}",
    )

    # 扣减库存（行锁保护下安全操作）
    product.stock -= 1
    product.total_claimed += 1
    await db.flush()

    # 如果关联了权益，触发权益领取
    claim_id = None
    if product.benefit_id:
        from app.services.campaign import claim_benefit

        result = await claim_benefit(
            db,
            tenant_id,
            product.benefit_id,
            str(consumer_id),
            idempotency_key=f"points_exchange:{product_id}:{consumer_id}",
        )
        if result.get("status") != "success" or not result.get("claim"):
            raise ValueError("权益发放失败")
        claim_id = result["claim"].get("id")

    redemption = PointRedemption(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        product_id=product_id,
        points_cost=product.points_cost,
        point_transaction_id=txn.id,
        benefit_id=product.benefit_id,
        benefit_claim_id=uuid.UUID(claim_id) if claim_id else None,
        status=PointRedemptionStatus.success,
    )
    db.add(redemption)
    await db.flush()
    await db.refresh(redemption)

    return {
        "redemption_id": str(redemption.id),
        "transaction_id": str(txn.id),
        "product_name": product.name,
        "points_spent": product.points_cost,
        "balance_after": txn.balance_after,
        "claim_id": claim_id,
    }
