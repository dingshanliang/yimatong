"""Authoritative diversion observation, evidence, and state transitions."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, get_request_security_credential
from app.models.channel import DiversionClue
from app.models.diversion_evidence import DiversionEvidence
from app.models.diversion_history import DiversionInvestigationHistory
from app.utils import utcnow

TERMINAL_STATUSES = {"confirmed_diversion", "false_positive", "normal_transfer"}


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


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
        raise HTTPException(status_code=403, detail="Diversion authority denied") from exc
    if sqlstate == "23503":
        raise HTTPException(status_code=404, detail="Diversion clue not found") from exc
    if sqlstate in {"22023", "23505", "23514"}:
        raise HTTPException(status_code=409, detail="Diversion authority conflict") from exc
    if sqlstate == "55P03":
        raise HTTPException(status_code=409, detail="Diversion clue busy", headers={"Retry-After": "1"}) from exc
    raise exc


async def _call(db: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        row = (await db.execute(text(sql), params)).mappings().one()
    except DBAPIError as exc:
        _map_error(exc)
        raise AssertionError("unreachable")
    return dict(row)


def _sqlite_receipt(db: AsyncSession, key: tuple, payload: dict[str, Any]) -> dict[str, Any] | None:
    receipts = db.sync_session.info.setdefault("diversion_authority_receipts", {})
    digest = _digest(payload)
    existing = receipts.get(key)
    if existing is not None and existing["digest"] != digest:
        raise HTTPException(status_code=409, detail="Diversion authority conflict")
    return existing


def _store_sqlite_receipt(db: AsyncSession, key: tuple, payload: dict[str, Any], result: dict[str, Any]) -> None:
    db.sync_session.info.setdefault("diversion_authority_receipts", {})[key] = {
        "digest": _digest(payload),
        "result": result,
    }


async def record_observation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    observation_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time,
    idempotency_key: str,
    public_id: str,
    code_item_id: uuid.UUID,
    ip_hash: str | None,
    detected_city: str,
    expected_region: str,
    location_source: str,
    location_accuracy: str,
    location_authorized: bool | None,
    distributor_id: uuid.UUID | None,
    region_id: uuid.UUID | None,
    rule_name: str,
    confidence: str,
) -> dict[str, Any]:
    if not _session_uses_postgresql(db):
        raise RuntimeError("SQLite observation recording remains in the legacy semantic adapter")
    return await _call(
        db,
        "SELECT * FROM public.record_diversion_observation("
        ":tenant,:observation,:scan_event,:scan_time,:idem,:public_id,:code_item,:ip_hash,:detected,:expected,"
        ":location_source,:location_accuracy,:location_authorized,:distributor,:region,:rule,:confidence)",
        {
            "tenant": tenant_id,
            "observation": observation_id,
            "scan_event": scan_event_id,
            "scan_time": scan_time,
            "idem": idempotency_key,
            "public_id": public_id,
            "code_item": code_item_id,
            "ip_hash": ip_hash,
            "detected": detected_city,
            "expected": expected_region,
            "location_source": location_source,
            "location_accuracy": location_accuracy,
            "location_authorized": location_authorized,
            "distributor": distributor_id,
            "region": region_id,
            "rule": rule_name,
            "confidence": confidence,
        },
    )


async def transition_clue(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    clue_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    to_status: str,
    reason: str,
    resolution_note: str | None,
    actor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    payload = {
        "clue_id": clue_id,
        "expected_version": expected_version,
        "to_status": to_status,
        "reason": reason,
        "resolution_note": resolution_note,
    }
    if _session_uses_postgresql(db):
        return await _call(
            db,
            "SELECT * FROM public.transition_diversion_clue("
            ":tenant,:sid,:audit,:clue,:version,:idem,:status,:reason,:note)",
            {
                "tenant": tenant_id,
                "sid": _auth_session_id(),
                "audit": uuid7(),
                "clue": clue_id,
                "version": expected_version,
                "idem": idempotency_key,
                "status": to_status,
                "reason": reason,
                "note": resolution_note,
            },
        )

    receipt_key = (tenant_id, "transition", idempotency_key)
    replay = _sqlite_receipt(db, receipt_key, payload)
    if replay is not None:
        return {**replay["result"], "replayed": True}
    clue = await db.scalar(
        select(DiversionClue).where(DiversionClue.tenant_id == tenant_id, DiversionClue.id == clue_id)
    )
    if clue is None:
        raise HTTPException(status_code=404, detail="Diversion clue not found")
    version = getattr(clue, "version", 1)
    if version != expected_version:
        raise HTTPException(status_code=409, detail="Diversion authority conflict")
    current = clue.investigation_status
    legal = (
        current in {"open", "pending_evidence"} and to_status in ({"open", "pending_evidence"} | TERMINAL_STATUSES)
    ) or (current in TERMINAL_STATUSES and to_status == "open")
    if not legal or current == to_status:
        raise HTTPException(status_code=409, detail="Diversion authority conflict")
    now = utcnow()
    actor_id = actor_id or uuid.uuid4()
    clue.investigation_status = to_status
    clue.resolved = to_status in TERMINAL_STATUSES
    if clue.resolved:
        clue.resolution_action = to_status
        clue.resolution_note = resolution_note
        clue.resolved_by_account_id = actor_id
        clue.resolved_at = now
    else:
        clue.resolved_by_account_id = None
        clue.resolved_at = None
    clue.version = version + 1
    db.add(
        DiversionInvestigationHistory(
            tenant_id=tenant_id,
            clue_id=clue_id,
            from_status=current,
            to_status=to_status,
            changed_by_account_id=actor_id,
            reason=reason,
        )
    )
    await db.flush()
    result = {
        "resource_id": clue_id,
        "clue_id": clue_id,
        "clue_version": clue.version,
        "investigation_status": to_status,
        "resolved": clue.resolved,
        "observation_count": clue.observation_count,
        "replayed": False,
        "actor_id": actor_id,
        "recorded_at": now,
    }
    _store_sqlite_receipt(db, receipt_key, payload, result)
    return result


async def add_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    clue_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    evidence_type: str,
    file_url: str | None,
    description: str | None,
    evidence_digest: str | None,
    actor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    evidence_id = uuid7()
    evidence_payload = {
        "clue_id": clue_id,
        "expected_version": expected_version,
        "evidence_type": evidence_type,
        "file_url": file_url,
        "description": description,
        "evidence_digest": evidence_digest,
    }
    payload_digest = _digest(evidence_payload)
    if _session_uses_postgresql(db):
        return await _call(
            db,
            "SELECT * FROM public.add_diversion_evidence("
            ":tenant,:sid,:audit,:evidence,:clue,:version,:idem,:type,:url,:description,:digest)",
            {
                "tenant": tenant_id,
                "sid": _auth_session_id(),
                "audit": uuid7(),
                "evidence": evidence_id,
                "clue": clue_id,
                "version": expected_version,
                "idem": idempotency_key,
                "type": evidence_type,
                "url": file_url,
                "description": description,
                "digest": evidence_digest or payload_digest,
            },
        )

    receipt_key = (tenant_id, "evidence", idempotency_key)
    replay = _sqlite_receipt(db, receipt_key, evidence_payload)
    if replay is not None:
        return {**replay["result"], "replayed": True}
    clue = await db.scalar(
        select(DiversionClue).where(DiversionClue.tenant_id == tenant_id, DiversionClue.id == clue_id)
    )
    if clue is None:
        raise HTTPException(status_code=404, detail="Diversion clue not found")
    version = getattr(clue, "version", 1)
    if version != expected_version:
        raise HTTPException(status_code=409, detail="Diversion authority conflict")
    actor_id = actor_id or uuid.uuid4()
    evidence = DiversionEvidence(
        id=evidence_id,
        tenant_id=tenant_id,
        clue_id=clue_id,
        evidence_type=evidence_type,
        source="brand_ops",
        file_url=file_url,
        description=description,
        uploaded_by_account_id=actor_id,
        evidence_digest=evidence_digest or payload_digest,
    )
    db.add(evidence)
    clue.version = version + 1
    await db.flush()
    result = {
        "resource_id": evidence_id,
        "clue_id": clue_id,
        "clue_version": clue.version,
        "investigation_status": clue.investigation_status,
        "resolved": clue.resolved,
        "observation_count": clue.observation_count,
        "replayed": False,
        "actor_id": actor_id,
        "recorded_at": utcnow(),
    }
    _store_sqlite_receipt(db, receipt_key, evidence_payload, result)
    return result
