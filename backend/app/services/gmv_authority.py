"""Application adapter for the database-owned external-order ledger authority."""

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.crypto import hash_phone


def canonical_order_payload_digest(payload: dict[str, Any]) -> str:
    """Hash a stable business representation; never hash float formatting."""

    def normalize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    canonical = json.dumps(normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def derive_order_event_idempotency_key(
    request_key: uuid.UUID,
    source_system: str,
    external_order_id: str,
) -> uuid.UUID:
    return uuid.uuid5(request_key, f"{source_system}:{external_order_id}")


async def record_external_order_value_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_system: str,
    external_order_id: str,
    event_type: str,
    amount: Decimal | None,
    currency: str,
    idempotency_key: uuid.UUID,
    payload_digest: str,
    provenance_digest: str,
    auth_session_id: uuid.UUID,
    occurred_at: datetime,
    order_time: datetime | None = None,
    phone: str | None = None,
    product_name: str | None = None,
    channel: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Call the sole SECURITY DEFINER writer for order value facts."""

    result = await db.execute(
        text(
            """
                SELECT *
                FROM public.record_external_order_value_event(
                    CAST(:tenant_id AS uuid), :source_system, :external_order_id,
                    :event_type, CAST(:amount AS numeric), :currency,
                    :idempotency_key, :payload_digest,
                    :provenance_digest, CAST(:auth_session_id AS uuid), :occurred_at,
                    :order_time, :phone_hash, :product_name, :channel, :reason
                )
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "source_system": source_system,
            "external_order_id": external_order_id,
            "event_type": event_type,
            "amount": amount,
            "currency": currency,
            "idempotency_key": str(idempotency_key),
            "payload_digest": payload_digest,
            "provenance_digest": provenance_digest,
            "auth_session_id": str(auth_session_id),
            "occurred_at": occurred_at,
            "order_time": order_time,
            "phone_hash": hash_phone(phone) if phone else None,
            "product_name": product_name,
            "channel": channel,
            "reason": reason,
        },
    )
    row = result.mappings().one()
    return dict(row)

