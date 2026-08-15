"""DB-authoritative risk rule, evaluation, and reversible pause operations."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, get_request_security_credential


def _auth_session_id() -> uuid.UUID:
    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=403, detail="A live login session is required")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Invalid login session") from exc


def _map_error(exc: DBAPIError) -> None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        raise HTTPException(status_code=403, detail="Risk authority denied") from exc
    if sqlstate == "23503":
        raise HTTPException(status_code=404, detail="Risk resource not found") from exc
    if sqlstate in {"22023", "23505", "23514"}:
        raise HTTPException(status_code=409, detail="Risk authority conflict") from exc
    if sqlstate == "55P03":
        raise HTTPException(status_code=409, detail="Risk resource busy", headers={"Retry-After": "1"}) from exc
    raise exc


async def _call(db: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    if not _session_uses_postgresql(db):
        raise RuntimeError("Risk mutations require PostgreSQL authority")
    try:
        row = (await db.execute(text(sql), params)).mappings().one()
    except DBAPIError as exc:
        _map_error(exc)
        raise AssertionError("unreachable")
    return dict(row)


async def mutate_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    action: str,
    rule_id: uuid.UUID,
    expected_version: int | None,
    idempotency_key: str,
    name: str | None,
    rule_type: str | None,
    rule_action: str | None,
    config: dict[str, Any] | None,
    enabled: bool | None,
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.mutate_risk_rule("
        ":tenant,:sid,:audit,:action,:rule,:version,:idem,:name,:type,:rule_action,CAST(:config AS jsonb),:enabled)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "action": action,
            "rule": rule_id,
            "version": expected_version,
            "idem": idempotency_key,
            "name": name,
            "type": rule_type,
            "rule_action": rule_action,
            "config": json.dumps(config) if config is not None else None,
            "enabled": enabled,
        },
    )


async def set_campaign_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    rule_id: uuid.UUID,
    campaign_id: uuid.UUID,
    attach: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.set_campaign_risk_rule(:tenant,:sid,:audit,:rule,:campaign,:attach,:idem)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "rule": rule_id,
            "campaign": campaign_id,
            "attach": attach,
            "idem": idempotency_key,
        },
    )


async def resume_campaign_pause(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    pause_id: uuid.UUID,
    expected_version: int,
    idempotency_key: str,
    reason: str,
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.resume_risk_campaign_pause(:tenant,:sid,:audit,:pause,:version,:idem,:reason)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "pause": pause_id,
            "version": expected_version,
            "idem": idempotency_key,
            "reason": reason,
        },
    )


async def mark_notification_read(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    notification_id: uuid.UUID,
    idempotency_key: str,
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.mark_risk_notification_read(:tenant,:sid,:audit,:notification,:idem)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "notification": notification_id,
            "idem": idempotency_key,
        },
    )


async def mark_all_notifications_read(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.mark_all_risk_notifications_read(:tenant,:sid,:audit,:idem)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "idem": idempotency_key,
        },
    )


async def evaluate_execute_scan(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    scan_event_id: uuid.UUID,
    rule_id: uuid.UUID,
    receipt_id: uuid.UUID,
    idempotency_key: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return await _call(
        db,
        "SELECT * FROM public.evaluate_execute_scan_risk(:tenant,:scan,:rule,:receipt,:idem,CAST(:context AS jsonb))",
        {
            "tenant": tenant_id,
            "scan": scan_event_id,
            "rule": rule_id,
            "receipt": receipt_id,
            "idem": idempotency_key,
            "context": json.dumps(context),
        },
    )
