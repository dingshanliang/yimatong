"""外部成交与 GMV 归因服务"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gmv import ExternalOrder, GmvAttribution
from app.models.member import ConsumerProfile
from app.utils.crypto import hash_phone


async def import_orders(
    db: AsyncSession, tenant_id: uuid.UUID, orders: list[dict],
) -> int:
    """批量导入外部订单"""
    count = 0
    for o in orders:
        order = ExternalOrder(
            tenant_id=tenant_id,
            external_id=o["external_id"],
            amount=o["amount"],
            phone_hash=hash_phone(o["phone"]) if o.get("phone") else None,
            product_name=o.get("product_name"),
            order_time=datetime.fromisoformat(o["order_time"].replace("Z", "+00:00")) if o.get("order_time") else None,
        )
        db.add(order)
        count += 1
    await db.commit()
    return count


async def list_orders(
    db: AsyncSession, tenant_id: uuid.UUID,
    page: int = 1, page_size: int = 20,
) -> tuple[list[ExternalOrder], int]:
    """查询外部订单"""
    count_stmt = select(func.count()).select_from(ExternalOrder).where(
        ExternalOrder.tenant_id == tenant_id,
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(ExternalOrder)
        .where(ExternalOrder.tenant_id == tenant_id)
        .order_by(ExternalOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def match_order(
    db: AsyncSession, tenant_id: uuid.UUID, match_by: str, value: str,
) -> dict:
    """按匹配规则关联订单与消费者/扫码"""
    if match_by == "phone":
        phone_h = hash_phone(value)
        consumer_result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_h,
            )
        )
        consumer = consumer_result.scalar_one_or_none()
        if consumer:
            # 标记匹配的订单
            order_result = await db.execute(
                select(ExternalOrder).where(
                    ExternalOrder.tenant_id == tenant_id,
                    ExternalOrder.phone_hash == phone_h,
                    ExternalOrder.matched.is_(False),
                )
            )
            for order in order_result.scalars().all():
                order.matched = True
                attr = GmvAttribution(
                    tenant_id=tenant_id,
                    external_order_id=order.id,
                    amount=order.amount,
                    match_type="phone",
                )
                db.add(attr)
            await db.commit()
            return {"matched": True, "consumer_id": str(consumer.id)}

    return {"matched": False}


async def get_gmv_dashboard(
    db: AsyncSession, tenant_id: uuid.UUID, group_by: str | None = None,
) -> dict:
    """GMV 归因看板"""
    total_gmv_result = await db.execute(
        select(func.coalesce(func.sum(GmvAttribution.amount), 0)).where(
            GmvAttribution.tenant_id == tenant_id,
        )
    )
    total_gmv = float(total_gmv_result.scalar() or 0)

    matched_result = await db.execute(
        select(func.count()).select_from(GmvAttribution).where(
            GmvAttribution.tenant_id == tenant_id,
        )
    )
    matched_orders = matched_result.scalar() or 0

    total_orders_result = await db.execute(
        select(func.count()).select_from(ExternalOrder).where(
            ExternalOrder.tenant_id == tenant_id,
        )
    )
    total_orders = total_orders_result.scalar() or 0

    result = {
        "total_gmv": total_gmv,
        "matched_orders": matched_orders,
        "total_orders": total_orders,
    }

    if group_by == "public_id":
        by_code_result = await db.execute(
            select(
                GmvAttribution.public_id,
                func.sum(GmvAttribution.amount).label("gmv"),
                func.count().label("orders"),
            )
            .where(GmvAttribution.tenant_id == tenant_id)
            .group_by(GmvAttribution.public_id)
        )
        result["by_public_id"] = [
            {"public_id": row.public_id, "gmv": float(row.gmv), "orders": row.orders}
            for row in by_code_result.all()
        ]

    return result


async def list_attributions(
    db: AsyncSession, tenant_id: uuid.UUID,
    page: int = 1, page_size: int = 20,
) -> tuple[list[GmvAttribution], int]:
    """查询归因记录"""
    count_stmt = select(func.count()).select_from(GmvAttribution).where(
        GmvAttribution.tenant_id == tenant_id,
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(GmvAttribution)
        .where(GmvAttribution.tenant_id == tenant_id)
        .order_by(GmvAttribution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
