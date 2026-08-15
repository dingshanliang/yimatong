"""Transactional webhook outbox recording and committed-event expansion."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.webhook import WebhookDelivery, WebhookDomainEvent, WebhookEndpoint

logger = logging.getLogger(__name__)
REDIS_QUEUE_KEY = "ymt:webhook:deliver_queue"


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def record_domain_event(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    event_type: str,
    data: dict,
    *,
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> WebhookDomainEvent:
    """Add one immutable event fact to the caller's transaction; never commit or enqueue."""

    event_id = event_id or uuid7()
    occurred_at = occurred_at or datetime.now(UTC)
    envelope = {
        "id": str(event_id),
        "type": event_type,
        "timestamp": occurred_at.isoformat(),
        "tenant_id": str(tenant_id),
        "data": data,
    }
    payload_digest = hashlib.sha256(_canonical_json(envelope)).hexdigest()
    event = WebhookDomainEvent(
        id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        payload=envelope,
        payload_digest=payload_digest,
        occurred_at=occurred_at,
    )
    db.add(event)
    await db.flush()
    return event


async def _enqueue_delivery(delivery_id: uuid.UUID) -> None:
    """Best-effort accelerator. Durable database polling remains authoritative."""

    try:
        import redis.asyncio as aioredis

        from app.core.config import settings

        async with aioredis.from_url(settings.redis_url) as redis:
            await redis.lpush(REDIS_QUEUE_KEY, str(delivery_id))
    except Exception:
        logger.warning("Failed to enqueue webhook delivery %s; DB poller will recover it", delivery_id)


async def expand_committed_events(limit: int = 100) -> int:
    """Lease committed event facts and materialize immutable endpoint snapshots."""

    from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context

    async with async_session_factory() as control_db:
        keys = await bootstrap_tenant_keys(
            control_db,
            select(WebhookDomainEvent.id, WebhookDomainEvent.tenant_id)
            .where(WebhookDomainEvent.expanded_at.is_(None))
            .order_by(WebhookDomainEvent.created_at, WebhookDomainEvent.id)
            .limit(limit),
        )

    created_ids: list[uuid.UUID] = []
    for event_id, tenant_id in keys:
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            event = await db.scalar(
                select(WebhookDomainEvent)
                .where(
                    WebhookDomainEvent.id == event_id,
                    WebhookDomainEvent.tenant_id == tenant_id,
                    WebhookDomainEvent.expanded_at.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if event is None:
                continue
            endpoints = list(
                (
                    await db.scalars(
                        select(WebhookEndpoint).where(
                            WebhookEndpoint.tenant_id == tenant_id,
                            WebhookEndpoint.enabled.is_(True),
                        )
                    )
                ).all()
            )
            for endpoint in endpoints:
                events = endpoint.events if isinstance(endpoint.events, list) else json.loads(endpoint.events)
                if event.event_type not in events:
                    continue
                delivery = WebhookDelivery(
                    tenant_id=tenant_id,
                    endpoint_id=endpoint.id,
                    event_id=str(event.id),
                    domain_event_id=event.id,
                    event_type=event.event_type,
                    payload=event.payload,
                    payload_digest=event.payload_digest,
                    endpoint_url=endpoint.url,
                    endpoint_secret_ciphertext=endpoint.secret_ciphertext,
                    endpoint_secret_nonce=endpoint.secret_nonce,
                    endpoint_secret_key_id=endpoint.secret_key_id,
                    endpoint_config_version=endpoint.config_version,
                    status="pending",
                )
                db.add(delivery)
                await db.flush()
                created_ids.append(delivery.id)
            event.expanded_at = datetime.now(UTC)
            await db.commit()

    for delivery_id in created_ids:
        await _enqueue_delivery(delivery_id)
    return len(created_ids)


def init_webhook_dispatcher() -> None:
    """Compatibility hook: durable expansion is driven by the worker poller."""

    logger.info("Transactional webhook outbox enabled")
