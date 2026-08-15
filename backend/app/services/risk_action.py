"""Thin application adapter for DB-authoritative scan risk execution.

The database function owns the receipt, immutable explainability snapshot,
alert/interception records, exact campaign selection, reversible pauses, and outbox.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import RiskRule
from app.services.risk_authority import evaluate_execute_scan


async def execute_risk_action(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    scan_event_id: uuid.UUID,
    rule: RiskRule,
    context: dict,
    idempotency_key: str,
) -> dict:
    """Evaluate one persisted scan/rule pair and execute it atomically."""

    receipt_id = uuid.uuid5(tenant_id, f"scan-risk:{scan_event_id}:{rule.id}")
    return await evaluate_execute_scan(
        db,
        tenant_id,
        scan_event_id=scan_event_id,
        rule_id=rule.id,
        receipt_id=receipt_id,
        idempotency_key=idempotency_key,
        context=context,
    )
