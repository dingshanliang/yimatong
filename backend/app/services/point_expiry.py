"""积分过期清理服务。

扫描已过期的积分收入记录，按 FIFO 方式扣减消费者余额。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.member import (
    ConsumerProfile,
    PointTransaction,
    PointTransactionType,
)
from app.utils import utcnow

logger = logging.getLogger(__name__)

BATCH_SIZE = 200


async def expire_points_batch() -> int:
    """批量处理过期积分。返回处理的消费者数量。"""
    now = utcnow()
    processed = 0

    async with async_session_factory() as db:
        # 查找所有已过期但未清零的收入记录
        result = await db.execute(
            select(PointTransaction)
            .where(
                PointTransaction.expires_at.is_not(None),
                PointTransaction.expires_at <= now,
                PointTransaction.txn_type == PointTransactionType.earning,
                PointTransaction.amount > 0,
            )
            .order_by(PointTransaction.expires_at)
            .limit(BATCH_SIZE)
        )
        expired_txns = list(result.scalars().all())
        if not expired_txns:
            return 0

        # 按 consumer_id 分组，计算每个消费者的总过期积分
        consumer_expired: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
        for txn in expired_txns:
            tid = txn.tenant_id
            cid = txn.consumer_id
            key = (tid, cid)
            consumer_expired[key] = (
                consumer_expired[key][0] + txn.amount if key in consumer_expired else txn.amount
            )

        # 对每个消费者扣减余额并创建过期记录
        for (tid, cid), total_expired in consumer_expired.items():
            try:
                # 获取消费者当前余额
                profile_result = await db.execute(
                    select(ConsumerProfile).where(
                        ConsumerProfile.id == cid,
                        ConsumerProfile.tenant_id == tid,
                    )
                )
                profile = profile_result.scalar_one_or_none()
                if not profile or profile.total_points <= 0:
                    continue

                deduct = min(total_expired, profile.total_points)
                new_balance = profile.total_points - deduct
                profile.total_points = new_balance

                expire_txn = PointTransaction(
                    tenant_id=tid,
                    consumer_id=cid,
                    amount=-deduct,
                    balance_after=new_balance,
                    txn_type=PointTransactionType.expired,
                    reason="积分过期清零",
                )
                db.add(expire_txn)
                processed += 1
            except Exception:
                logger.warning(
                    "Failed to expire points for consumer %s", cid, exc_info=True
                )

        await db.commit()

    # 标记已处理的过期记录（设 amount=0 防止重复处理）
    async with async_session_factory() as db:
        txn_ids = [t.id for t in expired_txns]
        if txn_ids:
            await db.execute(
                update(PointTransaction)
                .where(PointTransaction.id.in_(txn_ids))
                .values(amount=0)
            )
            await db.commit()

    logger.info("Expired points processed: %d consumers", processed)
    return processed
