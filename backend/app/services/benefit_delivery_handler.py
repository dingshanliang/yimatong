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
from app.models.campaign import Benefit, BenefitClaim, CampaignClaimOutbox
from app.models.connector import BenefitDelivery, Connector
from app.services.circuit_breaker import CircuitBreaker
from app.services.connectors import get_adapter
from app.services.connectors.coupon_pool import CouponPoolAdapter
from app.services.connectors.secrets import connector_with_runtime_secrets

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
    claim_id = data.get("claim_id")
    benefit_id = data.get("benefit_id")
    consumer_id = data.get("consumer_id")
    if not benefit_id or not consumer_id:
        return

    event_tenant_id = uuid.UUID(tenant_id)
    async with async_session_factory() as db:
        from app.core.database import set_session_tenant_context

        await set_session_tenant_context(db, event_tenant_id)
        # 查询权益
        benefit_result = await db.execute(
            select(Benefit).where(
                Benefit.id == uuid.UUID(benefit_id),
                Benefit.tenant_id == event_tenant_id,
            )
        )
        benefit = benefit_result.scalar_one_or_none()
        if not benefit or not benefit.connector_id:
            return  # 平台内权益，不需要外部发放
        if claim_id and await db.scalar(
            select(CampaignClaimOutbox.id).where(
                CampaignClaimOutbox.tenant_id == event_tenant_id,
                CampaignClaimOutbox.claim_id == uuid.UUID(claim_id),
            )
        ):
            return  # Canonical campaign outbox is the sole delivery authority.

        # 查询连接器
        conn_result = await db.execute(
            select(Connector).where(
                Connector.id == benefit.connector_id,
                Connector.tenant_id == benefit.tenant_id,
                Connector.tenant_id == event_tenant_id,
            )
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
                BenefitClaim.tenant_id == event_tenant_id,
                BenefitClaim.consumer_id == consumer_id,
            )
            .values(delivery_status="pending")
        )

        # 执行发放
        await _do_deliver(
            db,
            event_tenant_id,
            connector,
            consumer_id,
            benefit.config_json,
            benefit_id=benefit.id,
            claim_id=uuid.UUID(claim_id) if claim_id else None,
        )
        await db.commit()


async def _do_deliver(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    connector: Connector,
    consumer_id: str,
    benefit_config: dict,
    benefit_id: uuid.UUID | None = None,
    claim_id: uuid.UUID | None = None,
    update_claim_status: bool = False,
) -> BenefitDelivery:
    """执行外部发放，写入 BenefitDelivery 记录。"""
    if connector.tenant_id != tenant_id:
        raise ValueError("Connector does not belong to delivery tenant")
    if benefit_id is not None:
        benefit_exists = await db.scalar(
            select(Benefit.id).where(
                Benefit.id == benefit_id,
                Benefit.tenant_id == tenant_id,
                Benefit.connector_id == connector.id,
            )
        )
        if benefit_exists is None:
            raise ValueError("Benefit does not belong to delivery connector tenant")
    if claim_id is not None:
        claim_exists = await db.scalar(
            select(BenefitClaim.id).where(
                BenefitClaim.id == claim_id,
                BenefitClaim.tenant_id == tenant_id,
                BenefitClaim.benefit_id == benefit_id,
            )
        )
        if claim_exists is None:
            raise ValueError("Claim does not belong to delivery benefit tenant")
    cb = _get_circuit_breaker(connector)

    delivery = BenefitDelivery(
        tenant_id=tenant_id,
        connector_id=connector.id,
        benefit_id=benefit_id,
        claim_id=claim_id,
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
        return delivery

    try:
        runtime_connector = connector_with_runtime_secrets(connector)
        adapter = get_adapter(runtime_connector)

        if isinstance(adapter, CouponPoolAdapter):
            result = await adapter.deliver_from_pool(db, connector, consumer_id, claim_id)
        else:
            # 注入解密后的凭证到 connector.config（适配器内部使用）
            result = await adapter.deliver(runtime_connector, consumer_id, benefit_config)

        cb.record_success()
        delivery.status = DeliveryStatus.SUCCESS if result.status == "success" else DeliveryStatus.PENDING
        delivery.external_id = result.external_id
        delivery.external_data = result.external_data
        if delivery.status == DeliveryStatus.SUCCESS:
            delivery.next_retry_at = None

        # 更新 claim delivery_status
        if delivery.status == DeliveryStatus.SUCCESS and update_claim_status:
            await _update_claim_delivery_status(
                db,
                tenant_id,
                connector.id,
                consumer_id,
                "delivered",
                claim_id=claim_id,
            )

        db.add(delivery)
        await db.flush()
        return delivery

    except Exception as exc:
        cb.record_failure()
        logger.warning("Deliver benefit failed for connector %s: %s", connector.id, exc)
        delivery.retry_count = 0
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_BASE)
        db.add(delivery)
        await db.flush()
        return delivery


async def _update_claim_delivery_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    consumer_id: str,
    status: str,
    claim_id: uuid.UUID | None = None,
) -> None:
    """更新关联的 BenefitClaim 的 delivery_status。"""
    if claim_id:
        await db.execute(
            update(BenefitClaim)
            .where(
                BenefitClaim.id == claim_id,
                BenefitClaim.tenant_id == tenant_id,
            )
            .values(delivery_status=status)
        )
        return

    # 通过 benefit 找到关联的 claim
    await db.execute(
        update(BenefitClaim)
        .where(
            BenefitClaim.consumer_id == consumer_id,
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.benefit_id.in_(
                select(Benefit.id).where(
                    Benefit.tenant_id == tenant_id,
                    Benefit.connector_id == connector_id,
                )
            ),
        )
        .values(delivery_status=status)
    )


async def release_failed_delivery_reservation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
) -> bool:
    """旧版发放重试链终止时释放预留：claim 置 failed、退还库存与预算占用。

    仅处理仍处于 reserved 且业务有效的旧链路 claim（outbox 主链路由
    fail_campaign_claim_outbox 在数据库内权威释放）。幂等：重复调用返回 False。
    reserved_amount 保留为事实记录，reservation_status='refunded' 表达已退回。
    """

    claim = await db.scalar(
        select(BenefitClaim).where(
            BenefitClaim.id == claim_id,
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.reservation_status == "reserved",
            BenefitClaim.status.in_(("success", "claimed")),
        )
    )
    if claim is None:
        return False

    benefit = await db.scalar(
        select(Benefit).where(Benefit.id == claim.benefit_id, Benefit.tenant_id == tenant_id)
    )
    if benefit is not None:
        config = dict(benefit.config_json or {})
        claimed_budget = config.get("claimed_budget")
        if isinstance(claimed_budget, int) and claim.reserved_amount:
            config["claimed_budget"] = max(0, claimed_budget - claim.reserved_amount)
            benefit.config_json = config
        if benefit.stock_used > 0:
            benefit.stock_used -= 1

    claim.status = "failed"
    claim.delivery_status = "failed"
    claim.reservation_status = "refunded"
    return True


# 注册事件处理器
event_bus.add_handler("claim.created", on_claim_created)
