"""会员与积分服务层"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.member import (
    ConsumerProfile,
    MemberLevel,
    PointRule,
    PointTransaction,
    PointTransactionType,
)
from app.utils.crypto import encrypt_phone, hash_phone


async def get_or_create_consumer(
    db: AsyncSession, tenant_id: uuid.UUID, phone: str | None = None,
) -> ConsumerProfile:
    """获取或创建消费者档案"""
    if phone:
        phone_h = hash_phone(phone)
        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_h,
            )
        )
        consumer = result.scalar_one_or_none()
        if consumer:
            return consumer

    consumer = ConsumerProfile(
        tenant_id=tenant_id,
        phone_hash=hash_phone(phone) if phone else None,
        phone_encrypted=encrypt_phone(phone) if phone else None,
    )
    db.add(consumer)
    await db.commit()
    await db.refresh(consumer)
    return consumer


async def award_points(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID,
    points: int, reason: str, reference_id: str | None = None,
) -> PointTransaction:
    """发放积分"""
    consumer_result = await db.execute(
        select(ConsumerProfile).where(ConsumerProfile.id == consumer_id)
    )
    consumer = consumer_result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")

    new_balance = consumer.total_points + points
    consumer.total_points = new_balance

    txn = PointTransaction(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        amount=points,
        balance_after=new_balance,
        txn_type=PointTransactionType.earning,
        reason=reason,
        reference_id=reference_id,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    # 更新会员等级
    await _update_member_level(db, consumer)
    return txn


async def spend_points(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID,
    points: int, reason: str, reference_id: str | None = None,
) -> PointTransaction:
    """消费积分"""
    consumer_result = await db.execute(
        select(ConsumerProfile).where(ConsumerProfile.id == consumer_id)
    )
    consumer = consumer_result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")

    if consumer.total_points < points:
        raise ValueError("Insufficient points")

    new_balance = consumer.total_points - points
    consumer.total_points = new_balance

    txn = PointTransaction(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        amount=-points,
        balance_after=new_balance,
        txn_type=PointTransactionType.spending,
        reason=reason,
        reference_id=reference_id,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def _update_member_level(db: AsyncSession, consumer: ConsumerProfile):
    """根据积分更新会员等级"""
    old_level = consumer.member_level
    if consumer.total_points >= 10000:
        consumer.member_level = MemberLevel.platinum
    elif consumer.total_points >= 5000:
        consumer.member_level = MemberLevel.gold
    elif consumer.total_points >= 1000:
        consumer.member_level = MemberLevel.silver
    else:
        consumer.member_level = MemberLevel.normal

    if consumer.member_level != old_level:
        await db.commit()


async def get_point_rules(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[PointRule]:
    """获取租户积分规则"""
    result = await db.execute(
        select(PointRule).where(PointRule.tenant_id == tenant_id, PointRule.enabled.is_(True))
    )
    return list(result.scalars().all())


async def create_point_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_type: str, points: int,
) -> PointRule:
    """创建积分规则"""
    rule = PointRule(
        tenant_id=tenant_id,
        rule_type=rule_type,
        points=points,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def get_consumer_profile(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID,
) -> dict | None:
    """获取消费者积分概览"""
    result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
    )
    consumer = result.scalar_one_or_none()
    if not consumer:
        return None

    # 获取最近交易
    txn_result = await db.execute(
        select(PointTransaction)
        .where(PointTransaction.consumer_id == consumer_id)
        .order_by(PointTransaction.id.desc())
        .limit(10)
    )
    recent_txns = list(txn_result.scalars().all())

    return {
        "id": str(consumer.id),
        "member_level": consumer.member_level,
        "total_points": consumer.total_points,
        "recent_transactions": [
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
            }
            for t in recent_txns
        ],
    }


async def list_point_transactions(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID,
    page: int = 1, page_size: int = 20,
) -> tuple[list[PointTransaction], int]:
    """查询积分流水"""
    stmt = select(PointTransaction).where(
        PointTransaction.tenant_id == tenant_id,
        PointTransaction.consumer_id == consumer_id,
    )
    count_stmt = select(func.count()).select_from(PointTransaction).where(
        PointTransaction.tenant_id == tenant_id,
        PointTransaction.consumer_id == consumer_id,
    )

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(PointTransaction.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def get_consumer_phone(
    db: AsyncSession, consumer_id: uuid.UUID,
) -> str | None:
    """获取消费者脱敏手机号"""
    from app.utils.crypto import decrypt_phone, mask_phone

    result = await db.execute(
        select(ConsumerProfile).where(ConsumerProfile.id == consumer_id)
    )
    consumer = result.scalar_one_or_none()
    if not consumer or not consumer.phone_encrypted:
        return None
    try:
        return mask_phone(decrypt_phone(consumer.phone_encrypted))
    except Exception:
        return None
