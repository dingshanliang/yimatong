"""Opaque, token-bound identity for consumer benefit claims."""

from __future__ import annotations

import hashlib
import hmac
import uuid

from app.core.config import settings


def build_claim_idempotency_key(payload: dict, benefit_id: uuid.UUID) -> str:
    """Derive a bounded key without persisting the scan token or consumer PII."""

    values: list[str] = []
    for field in ("tenant_id", "public_id", "jti", "scan_event_id"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"scan token missing {field}")
        values.append(value.strip())
    authority = "\x1f".join([*values, str(benefit_id)])
    digest = hmac.new(settings.secret_key.encode(), authority.encode(), hashlib.sha256).hexdigest()
    return f"claim:v1:{digest}"


def build_claim_consumer_id(payload: dict) -> str:
    """Return a durable member id or an opaque anonymous visitor identity."""

    consumer_id = payload.get("consumer_id")
    if isinstance(consumer_id, str) and consumer_id.strip():
        try:
            return str(uuid.UUID(consumer_id.strip()))
        except ValueError as exc:
            raise ValueError("scan token has invalid consumer_id") from exc

    tenant_id = payload.get("tenant_id")
    public_id = payload.get("public_id")
    visitor_id = payload.get("visitor_id")
    if not all(isinstance(value, str) and value.strip() for value in (tenant_id, public_id, visitor_id)):
        raise ValueError("scan token missing anonymous consumer authority")
    authority = "\x1f".join((tenant_id.strip(), public_id.strip(), visitor_id.strip()))
    digest = hmac.new(settings.secret_key.encode(), authority.encode(), hashlib.sha256).hexdigest()
    return f"anon:v1:{digest}"
