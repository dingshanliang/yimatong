"""积分过期清理服务。

扫描已过期的积分收入记录，按 FIFO 方式扣减消费者余额。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context
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

    async with async_session_factory() as bootstrap_db:
        work_keys = await bootstrap_tenant_keys(
            bootstrap_db,
            select(PointTransaction.id, PointTransaction.tenant_id)
            .where(
                PointTransaction.expires_at.is_not(None),
                PointTransaction.expires_at <= now,
                PointTransaction.txn_type == PointTransactionType.earning,
                PointTransaction.amount > 0,
            )
            .order_by(PointTransaction.expires_at, PointTransaction.id)
            .limit(BATCH_SIZE),
        )
    if not work_keys:
        return 0

    tenant_work: dict[uuid.UUID, list[uuid.UUID]] = {}
    for txn_id, tenant_id in work_keys:
        tenant_work.setdefault(tenant_id, []).append(txn_id)

    for tenant_id, txn_ids in tenant_work.items():
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            expired_txns = list(
                (
                    await db.scalars(
                        select(PointTransaction)
                        .where(
                            PointTransaction.id.in_(txn_ids),
                            PointTransaction.tenant_id == tenant_id,
                            PointTransaction.amount > 0,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            consumer_expired: dict[uuid.UUID, int] = {}
            for txn in expired_txns:
                consumer_expired[txn.consumer_id] = consumer_expired.get(txn.consumer_id, 0) + txn.amount

            for cid, total_expired in consumer_expired.items():
                try:
                    profile = await db.scalar(
                        select(ConsumerProfile)
                        .where(ConsumerProfile.id == cid, ConsumerProfile.tenant_id == tenant_id)
                        .with_for_update()
                    )
                    if profile and profile.total_points > 0:
                        deduct = min(total_expired, profile.total_points)
                        new_balance = profile.total_points - deduct
                        profile.total_points = new_balance
                        db.add(
                            PointTransaction(
                                tenant_id=tenant_id,
                                consumer_id=cid,
                                amount=-deduct,
                                balance_after=new_balance,
                                txn_type=PointTransactionType.expired,
                                reason="积分过期清零",
                            )
                        )
                        processed += 1
                except Exception:
                    logger.warning("Failed to expire points for consumer %s", cid, exc_info=True)
            for txn in expired_txns:
                txn.amount = 0
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    logger.info("Expired points processed: %d consumers", processed)
    return processed
