"""积分规则自动执行处理器。

订阅 scan.created 等事件，根据租户配置的积分规则自动发放积分。
支持每日限额、幂等去重。
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.event_bus import event_bus
from app.models.member import PointRule, PointTransaction, PointTransactionType
from app.utils import utcnow

logger = logging.getLogger(__name__)

DEDUP_KEY_PREFIX = "ymt:points:dedup:"
DEDUP_TTL_SECONDS = 300  # 5 分钟冷却


async def _check_daily_limit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    rule_type: str,
    daily_limit: int,
) -> bool:
    """检查今日是否已达每日限额。0 表示无限制。"""
    if daily_limit <= 0:
        return False  # 无限制

    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count()).select_from(PointTransaction).where(
            PointTransaction.tenant_id == tenant_id,
            PointTransaction.consumer_id == consumer_id,
            PointTransaction.txn_type == PointTransactionType.earning,
            PointTransaction.reason == f"auto:{rule_type}",
            PointTransaction.created_at >= today_start,
        )
    )
    count = result.scalar() or 0
    return count >= daily_limit


async def _check_redis_dedup(tenant_id: str, consumer_id: str, rule_type: str) -> bool:
    """Redis 去重防止短时间内重复发放。"""
    try:
        import redis.asyncio as aioredis

        from app.core.config import settings

        key = f"{DEDUP_KEY_PREFIX}{tenant_id}:{consumer_id}:{rule_type}"
        async with aioredis.from_url(settings.redis_url) as r:
            exists = await r.exists(key)
            if exists:
                return True
            await r.setex(key, DEDUP_TTL_SECONDS, "1")
            return False
    except Exception:
        return False


async def _award_for_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    rule: PointRule,
    reference_id: str | None = None,
) -> PointTransaction | None:
    """根据规则发放积分，含每日限额检查和去重。"""
    # 每日限额检查
    if await _check_daily_limit(db, tenant_id, consumer_id, rule.rule_type, rule.daily_limit):
        return None

    # Redis 去重
    if await _check_redis_dedup(str(tenant_id), str(consumer_id), rule.rule_type):
        return None

    from app.services.member import award_points

    points_ttl_days = (rule.config or {}).get("points_ttl_days", 0)
    expires_at = None
    if points_ttl_days > 0:
        expires_at = utcnow() + timedelta(days=points_ttl_days)

    txn = await award_points(
        db,
        tenant_id,
        consumer_id,
        rule.points,
        f"auto:{rule.rule_type}",
        reference_id=reference_id,
    )
    if expires_at and txn:
        txn.expires_at = expires_at
        await db.flush()
    return txn


async def _handle_scan_created(event_type: str, data: dict, tenant_id: str) -> None:
    """scan.created 事件处理器：查找匹配的积分规则并自动发放。"""
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    public_id = data.get("public_id", "")

    async with async_session_factory() as db:
        try:
            tid = uuid.UUID(tenant_id)
            cid = uuid.UUID(consumer_id)

            # 查找该租户启用的扫码相关规则
            result = await db.execute(
                select(PointRule).where(
                    PointRule.tenant_id == tid,
                    PointRule.enabled.is_(True),
                    PointRule.rule_type.in_(["scan", "first_scan"]),
                )
            )
            rules = list(result.scalars().all())
            if not rules:
                return

            for rule in rules:
                try:
                    if rule.rule_type == "first_scan":
                        # 首扫奖励：检查是否是消费者的第一次扫码
                        scan_count_result = await db.execute(
                            select(func.count()).select_from(PointTransaction).where(
                                PointTransaction.tenant_id == tid,
                                PointTransaction.consumer_id == cid,
                                PointTransaction.reason == "auto:scan",
                            )
                        )
                        if (scan_count_result.scalar() or 0) > 0:
                            continue

                    await _award_for_rule(
                        db, tid, cid, rule, reference_id=f"scan:{public_id}"
                    )
                except Exception:
                    logger.warning(
                        "Failed to award points for rule %s", rule.rule_type, exc_info=True
                    )

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Error in point auto handler for scan.created")


async def _handle_consumer_created(event_type: str, data: dict, tenant_id: str) -> None:
    """consumer.created 事件处理器：发放注册积分（如果配置了 register 规则）。"""
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    async with async_session_factory() as db:
        try:
            tid = uuid.UUID(tenant_id)
            cid = uuid.UUID(consumer_id)

            result = await db.execute(
                select(PointRule).where(
                    PointRule.tenant_id == tid,
                    PointRule.enabled.is_(True),
                    PointRule.rule_type == "register",
                )
            )
            rule = result.scalar_one_or_none()
            if rule:
                await _award_for_rule(db, tid, cid, rule, reference_id="register")
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Error in point auto handler for consumer.created")


def init_point_auto_handler() -> None:
    """初始化积分自动发放处理器（在 lifespan 中调用）。"""
    event_bus.add_handler("scan.created", _handle_scan_created)
    event_bus.add_handler("consumer.created", _handle_consumer_created)
    logger.info("Point auto handler initialized")
