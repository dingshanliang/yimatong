"""外部权益连接器服务 — L2-L4

L2: 同步库存 (sync_stock)
L3: 发放权益 + 状态回传 (deliver_benefit, delivery_callback)
L4: 失败重试 (retry_delivery, get_pending_retries)
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import BenefitDelivery, Connector
from app.services.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------


class DeliveryStatus:
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


DEFAULT_MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2  # 指数退避基数（秒）

# 每个连接器的熔断器实例缓存（connector_id -> CircuitBreaker）
_circuit_breakers: dict[uuid.UUID, CircuitBreaker] = {}


def _get_circuit_breaker(connector_id: uuid.UUID) -> CircuitBreaker:
    if connector_id not in _circuit_breakers:
        _circuit_breakers[connector_id] = CircuitBreaker(threshold=5, reset_timeout=30)
    return _circuit_breakers[connector_id]


class ExternalBenefitError(Exception):
    """外部权益操作异常"""


# ---------------------------------------------------------------------------
# 内部 HTTP 调用
# ---------------------------------------------------------------------------


async def _fetch_external_stock(connector: Connector) -> dict:
    """调用外部系统获取库存"""
    api_url = connector.config.get("api_url", "")
    api_key = connector.config.get("api_key", "")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{api_url}/stock",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _call_external_deliver(
    connector: Connector,
    consumer_id: str,
    benefit_type: str,
    benefit_config: dict,
) -> dict:
    """调用外部系统发放权益"""
    api_url = connector.config.get("api_url", "")
    api_key = connector.config.get("api_key", "")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{api_url}/deliver",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "consumer_id": consumer_id,
                "benefit_type": benefit_type,
                "benefit_config": benefit_config,
            },
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# L2: 同步库存
# ---------------------------------------------------------------------------


async def sync_stock(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
) -> dict:
    """从外部系统同步库存到本地"""
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.tenant_id == tenant_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise ExternalBenefitError("Connector not found")
    if not connector.enabled:
        raise ExternalBenefitError("Connector is disabled")

    cb = _get_circuit_breaker(connector_id)
    if not cb.is_available():
        raise ExternalBenefitError("Circuit breaker open, stock sync skipped")

    try:
        stock_data = await _fetch_external_stock(connector)
        cb.record_success()
    except Exception as exc:
        cb.record_failure()
        logger.warning("Stock sync failed for connector %s: %s", connector_id, exc)
        raise ExternalBenefitError(f"External stock sync failed: {exc}") from exc

    # 更新 connector config 中的 stock 信息
    connector.config = {**connector.config, "stock": stock_data}
    await db.flush()
    await db.refresh(connector)

    return {
        "connector_id": str(connector.id),
        **stock_data,
    }


# ---------------------------------------------------------------------------
# L3: 发放权益 + 状态回传
# ---------------------------------------------------------------------------


async def deliver_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    connector: Connector,
    consumer_id: str,
    benefit_type: str,
    benefit_config: dict,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """向外部系统发放权益"""
    cb = _get_circuit_breaker(connector.id)

    delivery = BenefitDelivery(
        tenant_id=tenant_id,
        connector_id=connector.id,
        consumer_id=consumer_id,
        benefit_type=benefit_type,
        benefit_config=benefit_config,
        status=DeliveryStatus.PENDING,
        max_retries=max_retries,
    )

    if not cb.is_available():
        # 熔断器打开，记录为 pending，不调用外部
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_BASE)
        db.add(delivery)
        await db.flush()
        return _delivery_to_dict(delivery)

    try:
        ext_result = await _call_external_deliver(connector, consumer_id, benefit_type, benefit_config)
        cb.record_success()
        delivery.status = DeliveryStatus.SUCCESS
        delivery.external_data = ext_result
        db.add(delivery)
        await db.flush()
        return _delivery_to_dict(delivery)
    except Exception as exc:
        cb.record_failure()
        logger.warning("Deliver benefit failed: %s", exc)
        delivery.retry_count = 0
        delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_BASE)
        db.add(delivery)
        await db.flush()
        return _delivery_to_dict(delivery)


async def delivery_callback(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
    status: str,
    external_data: dict,
) -> dict:
    """接收外部系统的发放结果回调"""
    result = await db.execute(
        select(BenefitDelivery).where(
            BenefitDelivery.id == delivery_id,
            BenefitDelivery.tenant_id == tenant_id,
        )
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise ExternalBenefitError("Delivery not found")

    delivery.status = status
    delivery.external_data = external_data
    if status == DeliveryStatus.SUCCESS:
        delivery.next_retry_at = None

    await db.flush()
    return _delivery_to_dict(delivery)


# ---------------------------------------------------------------------------
# L4: 失败重试
# ---------------------------------------------------------------------------


async def retry_delivery(
    db: AsyncSession,
    delivery_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    """重试失败的权益发放"""
    result = await db.execute(
        select(BenefitDelivery).where(
            BenefitDelivery.id == delivery_id,
            BenefitDelivery.tenant_id == tenant_id,
        )
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise ExternalBenefitError("Delivery not found")

    if delivery.status == DeliveryStatus.SUCCESS:
        return _delivery_to_dict(delivery)

    if delivery.status == DeliveryStatus.FAILED:
        return _delivery_to_dict(delivery)

    # 获取连接器
    conn_result = await db.execute(select(Connector).where(Connector.id == delivery.connector_id))
    connector = conn_result.scalar_one_or_none()
    if not connector:
        delivery.status = DeliveryStatus.FAILED
        await db.flush()
        return _delivery_to_dict(delivery)

    cb = _get_circuit_breaker(connector.id)

    try:
        ext_result = await _call_external_deliver(
            connector,
            delivery.consumer_id,
            delivery.benefit_type,
            delivery.benefit_config,
        )
        cb.record_success()
        delivery.status = DeliveryStatus.SUCCESS
        delivery.external_data = ext_result
        delivery.next_retry_at = None
        await db.flush()
        return _delivery_to_dict(delivery)
    except Exception as exc:
        cb.record_failure()
        delivery.retry_count += 1
        logger.warning("Retry delivery %s failed (attempt %d): %s", delivery_id, delivery.retry_count, exc)

        if delivery.retry_count >= delivery.max_retries:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_retry_at = None
        else:
            backoff = RETRY_BACKOFF_BASE**delivery.retry_count
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=backoff)

        await db.flush()
        return _delivery_to_dict(delivery)


async def get_pending_retries(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[BenefitDelivery]:
    """获取待重试的发放记录"""
    now = datetime.now(UTC)
    result = await db.execute(
        select(BenefitDelivery)
        .where(
            BenefitDelivery.tenant_id == tenant_id,
            BenefitDelivery.status == DeliveryStatus.PENDING,
            BenefitDelivery.retry_count < BenefitDelivery.max_retries,
            BenefitDelivery.next_retry_at <= now,
        )
        .order_by(BenefitDelivery.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _delivery_to_dict(delivery: BenefitDelivery) -> dict:
    return {
        "id": str(delivery.id),
        "tenant_id": str(delivery.tenant_id),
        "connector_id": str(delivery.connector_id),
        "consumer_id": delivery.consumer_id,
        "benefit_type": delivery.benefit_type,
        "status": delivery.status,
        "retry_count": delivery.retry_count,
        "max_retries": delivery.max_retries,
        "external_data": delivery.external_data,
        "external_code": (delivery.external_data or {}).get("code"),
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
    }
