"""现金红包领取服务。

封装红包领取的核心流程：FOR UPDATE 读最新状态 → 金额计算 → 乐观锁扣减预算
→ 创建 claim + delivery → 执行微信转账。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class RedPacketTransferFailed(RuntimeError):
    """微信转账明确失败；预算/库存已在服务层回滚并写入 failed claim。

    调用方应当 commit（持久化补偿与 failed 审计记录）后再向用户返回失败，
    而不是回滚——回滚会把回滚后的预算扣减和 failed 记录一起撤销，丢失审计。
    与"红包已抢光/预算用尽"等扣减前抛出的 RuntimeError 区分：那些情况没有
    需要持久化的补偿，调用方回滚即可。
    """


# 乐观锁预算扣减 SQL
_DEDUCT_BUDGET_SQL = text("""
    UPDATE benefits
    SET config_json = jsonb_set(
        config_json, '{claimed_budget}',
        (COALESCE((config_json->>'claimed_budget')::int, 0) + :amount)::text::jsonb
    ),
    stock_used = stock_used + 1
    WHERE id = :benefit_id
    AND stock_used < stock_total
    AND (COALESCE((config_json->>'claimed_budget')::int, 0) + :amount) <= (config_json->>'budget')::int
""")

# 失败补偿：把刚扣掉的预算和库存加回去。stock_used > 0 守卫防止并发回滚越界。
_REFUND_BUDGET_SQL = text("""
    UPDATE benefits
    SET config_json = jsonb_set(
        config_json, '{claimed_budget}',
        GREATEST(COALESCE((config_json->>'claimed_budget')::int, 0) - :amount, 0)::text::jsonb
    ),
    stock_used = stock_used - 1
    WHERE id = :benefit_id
    AND stock_used > 0
""")


async def deduct_redpacket_budget(db: AsyncSession, benefit_id: uuid.UUID, amount: int) -> int:
    """乐观锁扣减红包预算与库存。返回受影响行数（0 = 抢光/预算不足）。

    单独成函数便于测试替换（SQLite 没有 jsonb_set），生产路径用 PG 专属 SQL。
    """
    result = await db.execute(_DEDUCT_BUDGET_SQL, {"benefit_id": benefit_id, "amount": amount})
    return result.rowcount or 0


async def refund_redpacket_budget(db: AsyncSession, benefit_id: uuid.UUID, amount: int) -> int:
    """失败补偿：加回预算与库存。返回受影响行数。"""
    result = await db.execute(_REFUND_BUDGET_SQL, {"benefit_id": benefit_id, "amount": amount})
    return result.rowcount or 0


async def claim_red_packet(
    db: AsyncSession,
    benefit_id: uuid.UUID,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    consumer_id: str,
    openid: str,
    total_count: int,
) -> dict:
    """执行红包领取的完整流程。

    Args:
        db: 数据库 session
        benefit_id: 权益 ID
        tenant_id: 租户 ID
        connector_id: 微信支付 connector ID
        consumer_id: 消费者 ID（字符串）
        openid: 微信 OpenID
        total_count: 该消费者已领取次数（用于幂等 key）

    Returns:
        {"amount": int, "claim_id": UUID, "status": str, "delivery_id": UUID}

    Raises:
        RuntimeException: 预算用尽、红包抢光等
    """
    from app.models.campaign import Benefit, BenefitClaim
    from app.models.connector import BenefitDelivery
    from app.services.connectors.registry import get_adapter
    from app.services.redpacket_amount import calc_amount, validate_config

    # 1. FOR UPDATE 读取最新库存和 config_json
    fresh = await db.execute(select(Benefit).where(Benefit.id == benefit_id).with_for_update())
    benefit = fresh.scalar_one_or_none()
    if not benefit or benefit.stock_used >= benefit.stock_total:
        raise RuntimeError("红包已抢光")

    config = benefit.config_json
    valid, msg = validate_config(config)
    if not valid:
        raise ValueError(f"Invalid red packet config: {msg}")

    # 2. 计算金额
    claimed_budget = config.get("claimed_budget", 0)
    budget = config.get("budget", 0)
    remaining = budget - claimed_budget

    if remaining <= 0:
        raise RuntimeError("红包预算已用尽")

    if config.get("amount_type") == "lucky":
        remaining_count = max(benefit.stock_total - benefit.stock_used, 1)
        amount = calc_amount(config, remaining_budget=remaining, remaining_count=remaining_count)
    else:
        amount = calc_amount(config)

    if amount > remaining:
        amount = remaining

    # 3. 乐观锁扣减预算
    if await deduct_redpacket_budget(db, benefit_id, amount) == 0:
        raise RuntimeError("红包已抢光")

    # 4. 创建 BenefitClaim
    idempotency_key = f"rp:{consumer_id}:{benefit_id}:{total_count}"
    claim = BenefitClaim(
        tenant_id=tenant_id,
        benefit_id=benefit_id,
        campaign_id=benefit.campaign_id,
        consumer_id=consumer_id,
        idempotency_key=idempotency_key,
        status="claimed",
    )
    db.add(claim)

    # 5. 创建 BenefitDelivery
    delivery = BenefitDelivery(
        tenant_id=tenant_id,
        connector_id=connector_id,
        consumer_id=consumer_id,
        benefit_type="cash_red_packet",
        benefit_config={
            "amount": amount,
            "openid": openid,
            "out_bill_no": str(claim.id),
            "transfer_remark": config.get("transfer_remark", "扫码领红包"),
        },
        status="pending",
    )
    db.add(delivery)
    await db.flush()

    # 6. 执行微信转账
    from app.models.connector import Connector

    conn_result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = conn_result.scalar_one_or_none()

    if connector is None:
        # 没有可用 connector：保持 pending，交给现有 BenefitDelivery 重试/对账路径。
        return {
            "amount": amount,
            "claim_id": claim.id,
            "status": "pending",
            "delivery_id": delivery.id,
        }

    adapter = get_adapter(connector)
    delivery_result = await adapter.deliver(connector, consumer_id, delivery.benefit_config)
    delivery.status = delivery_result.status
    delivery.external_data = delivery_result.external_data

    if delivery_result.status == "success":
        claim.status = "delivered"
        return {
            "amount": amount,
            "claim_id": claim.id,
            "status": "success",
            "delivery_id": delivery.id,
        }

    if delivery_result.status == "failed":
        # DC-01 补偿：转账明确失败时立即把预算和库存加回去，并把 claim 标记为
        # failed。这样品牌的红包预算不会被一次失败的转账永久占用（之前的实现
        # 会留下 status=claimed + 已扣预算但用户没收到钱，需要人工对账）。
        # pending（超时/可重试）不回滚：交给现有 BenefitDelivery 重试队列，
        # 后续重试或异步查询转账明细时再决定最终状态。
        await refund_redpacket_budget(db, benefit_id, amount)
        claim.status = "failed"
        await db.flush()
        logger.warning(
            "Red packet transfer failed, budget refunded: benefit=%s claim=%s msg=%s",
            benefit_id,
            claim.id,
            delivery_result.message,
        )
        raise RedPacketTransferFailed(f"红包转账失败：{delivery_result.message or 'transfer failed'}")

    # pending（超时 / 429 / 502 / 503）：保持 claim=claimed、delivery=pending，
    # 现有 BenefitDelivery 重试机制（benefit_delivery_handler + next_retry_at）
    # 负责后续投递或异步查询转账明细确认最终结果。
    return {
        "amount": amount,
        "claim_id": claim.id,
        "status": "pending",
        "delivery_id": delivery.id,
    }
