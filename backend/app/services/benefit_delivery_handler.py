"""权益领取事件 → 连接器发放处理器。

当消费者领取权益（claim.created）时，
如果该权益关联了外部连接器，自动触发发放。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.event_bus import event_bus
from app.models.campaign import Benefit, BenefitClaim
from app.models.connector import BenefitDelivery, Connector
from app.services.circuit_breaker import CircuitBreaker
from app.services.connectors import get_adapter
from app.services.connectors.base import DeliveryResult
from app.services.connectors.coupon_pool import CouponPoolAdapter
from app.services.connectors.secrets import decrypt_secrets

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2

_circuit_breakers: dict[uuid.UUID, CircuitBreaker] = {}


def _get_circuit_breaker(connector: Connector) -> CircuitBreaker:
    conn_id = connector.id
    if conn_id not in _circuit_breakers:
        cb_config = connector.config.get("circuit_breaker", {})
        threshold = cb_config.get("threshold", 5)
        reset_timeout = cb_config.get("reset_timeout", 30)
        _circuit_breakers[conn_id] = CircuitBreaker(threshold=threshold, reset_timeout=reset_timeout)
    return _circuit_breakers[conn_id]


class DeliveryStatus:
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


async def on_claim_created(event_type: str, data: dict, tenant_id: str) -> None:
    """claim.created 事件处理器：触发外部连接器发放。"""
    benefit_id = data.get("benefit_id")
    consumer_id = data.get("consumer_id")
    if not benefit_id or not consumer_id:
        return

    async with async_session_factory() as db:
        # 查询权益
        benefit_result = await db.execute(
            select(Benefit).where(Benefit.id == uuid.UUID(benefit_id))
        )
        benefit = benefit_result.scalar_one_or_none()
        if not benefit or not benefit.connector_id:
            return  # 平台内权益，不需要外部发放

        # 查询连接器
        conn_result = await db.execute(
            select(Connector).where(Connector.id == benefit.connector_id)
        )
        connector = conn_result.scalar_one_or_none()
        if not connector or not connector.enabled:
            logger.warning("Connector %s not found or disabled for benefit %s", benefit.connector_id, benefit_id)
            return

        # 更新 claim 的 delivery_status
        await db.execute(
            update(BenefitClaim)
            .where(
                BenefitClaim.benefit_id == benefit.id,
                BenefitClaim.consumer_id == consumer_id,
            )
            .values(delivery_status="pending")
        )

        # 执行发放
        await _do_deliver(db, uuid.UUID(tenant_id), connector, consumer_id, benefit.config_json)
        await db.commit()


async def _do_deliver(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    connector: Connector,
    consumer_id: str,
    benefit_config: dict,
) -> None:
    """执行外部发放，写入 BenefitDelivery 记录。"""
    cb = _get_circuit_breaker(connector)

    delivery = BenefitDelivery(
        tenant_id=tenant_id,
        connector_id=connector.id,
        consumer_id=consumer_id,
        benefit_type=benefit_config.get("benefit_type", "coupon"),
        benefit_config=benefit_config,
        status=DeliveryStatus.PENDING,
        max_retries=DEFAULT_MAX_RETRIES,
    )

    if not cb.is_available():
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_BASE)
        db.add(delivery)
        await db.flush()
        return

    try:
        adapter = get_adapter(connector)

        if isinstance(adapter, CouponPoolAdapter):
            result = await adapter.deliver_from_pool(db, connector, consumer_id)
        else:
            # 注入解密后的凭证到 connector.config（适配器内部使用）
            if connector.secrets_encrypted:
                secrets = decrypt_secrets(connector.secrets_encrypted)
                connector.config = {**connector.config, **secrets}

            result = await adapter.deliver(connector, consumer_id, benefit_config)

        cb.record_success()
        delivery.status = DeliveryStatus.SUCCESS if result.status == "success" else DeliveryStatus.PENDING
        delivery.external_data = result.external_data
        if delivery.status == DeliveryStatus.SUCCESS:
            delivery.next_retry_at = None

        # 更新 claim delivery_status
        if delivery.status == DeliveryStatus.SUCCESS:
            await _update_claim_delivery_status(db, connector.id, consumer_id, "delivered")

        db.add(delivery)
        await db.flush()

    except Exception as exc:
        cb.record_failure()
        logger.warning("Deliver benefit failed for connector %s: %s", connector.id, exc)
        delivery.retry_count = 0
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_BASE)
        db.add(delivery)
        await db.flush()


async def _update_claim_delivery_status(
    db: AsyncSession,
    connector_id: uuid.UUID,
    consumer_id: str,
    status: str,
) -> None:
    """更新关联的 BenefitClaim 的 delivery_status。"""
    # 通过 benefit 找到关联的 claim
    await db.execute(
        update(BenefitClaim)
        .where(
            BenefitClaim.consumer_id == consumer_id,
            BenefitClaim.benefit_id.in_(
                select(Benefit.id).where(Benefit.connector_id == connector_id)
            ),
        )
        .values(delivery_status=status)
    )


# 注册事件处理器
event_bus.add_handler("claim.created", on_claim_created)
