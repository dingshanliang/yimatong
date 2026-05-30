"""Webhook 事件调度器。

订阅事件总线，查找匹配的 webhook endpoint，创建投递记录并入队 Redis。
Worker 从 Redis 取出执行实际 HTTP 投递。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services.webhook_sender import build_envelope

logger = logging.getLogger(__name__)

REDIS_QUEUE_KEY = "ymt:webhook:deliver_queue"


async def _enqueue_delivery(delivery_id: str) -> None:
    """将投递 ID 推入 Redis List 队列。"""
    try:
        import redis.asyncio as aioredis

        from app.core.config import settings

        async with aioredis.from_url(settings.redis_url) as r:
            await r.lpush(REDIS_QUEUE_KEY, delivery_id)
    except Exception:
        logger.warning("Failed to enqueue webhook delivery %s to Redis, will rely on DB poller", delivery_id)


async def _dispatch_to_endpoints(
    db: AsyncSession,
    event_type: str,
    data: dict,
    tenant_id: str,
) -> None:
    """查找匹配的 webhook endpoint，为每个创建投递记录。"""
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.enabled.is_(True),
        )
    )
    endpoints = list(result.scalars().all())

    for ep in endpoints:
        events: list[str] = ep.events if isinstance(ep.events, list) else json.loads(ep.events)
        if event_type not in events:
            continue

        envelope = build_envelope(event_type, data, tenant_id)
        delivery = WebhookDelivery(
            tenant_id=tenant_id,
            endpoint_id=ep.id,
            event_id=envelope["id"],
            event_type=event_type,
            payload=envelope,
            status="pending",
        )
        db.add(delivery)
        await db.flush()

        await _enqueue_delivery(str(delivery.id))


async def _handle_event(event_type: str, data: dict, tenant_id: str) -> None:
    """事件总线处理器入口。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        try:
            await _dispatch_to_endpoints(db, event_type, data, tenant_id)
            await db.commit()
        except Exception:
            logger.exception("Webhook dispatcher error for %s", event_type)
            await db.rollback()


def init_webhook_dispatcher() -> None:
    """应用启动时调用，注册所有事件类型的 dispatcher。"""
    event_types = [
        "scan.created",
        "claim.created",
        "claim.used",
        "claim.expired",
        "consumer.created",
        "consumer.profile_updated",
        "risk.alert",
        "campaign.started",
        "campaign.ended",
    ]
    for et in event_types:
        event_bus.add_handler(et, _handle_event)
    logger.info("Webhook dispatcher registered for %d event types", len(event_types))
