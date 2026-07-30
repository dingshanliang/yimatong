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
    result = await db.execute(
        _DEDUCT_BUDGET_SQL,
        {"benefit_id": benefit_id, "amount": amount},
    )
    if result.rowcount == 0:
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

    if connector:
        adapter = get_adapter(connector)
        delivery_result = await adapter.deliver(connector, consumer_id, delivery.benefit_config)

        delivery.status = delivery_result.status
        delivery.external_data = delivery_result.external_data
        if delivery_result.status == "success":
            claim.status = "delivered"

        return {
            "amount": amount,
            "claim_id": claim.id,
            "status": delivery_result.status,
            "delivery_id": delivery.id,
        }

    return {
        "amount": amount,
        "claim_id": claim.id,
        "status": "pending",
        "delivery_id": delivery.id,
    }
