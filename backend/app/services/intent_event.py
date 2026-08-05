"""Tenant-safe, atomic persistence for intent telemetry."""

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.intent_event import IntentEvent


async def insert_intent_event_idempotent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    client_event_id: str,
    public_id: str | None = None,
    visitor_id: str | None = None,
    page_version_id: str | None = None,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    occurred_at: datetime | None = None,
) -> uuid.UUID | None:
    """Insert once per ``(tenant_id, client_event_id)``.

    Returns the new event ID, or ``None`` when a concurrent/existing request
    already owns the idempotency key. Transaction ownership remains with the
    caller.
    """

    values = {
        "id": uuid7(),
        "tenant_id": tenant_id,
        "event_type": event_type,
        "public_id": public_id,
        "visitor_id": visitor_id,
        "client_event_id": client_event_id,
        "page_version_id": page_version_id,
        "ip_hash": ip_hash,
        "user_agent": user_agent,
    }
    if occurred_at is not None:
        values["occurred_at"] = occurred_at

    insert_factory = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = (
        insert_factory(IntentEvent)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[IntentEvent.tenant_id, IntentEvent.client_event_id],
            index_where=IntentEvent.client_event_id.is_not(None),
        )
        .returning(IntentEvent.id)
    )
    return (await db.execute(statement)).scalar_one_or_none()
