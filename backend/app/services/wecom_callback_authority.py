"""Callback-role authority for verified Enterprise WeChat contact events."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from uuid6 import uuid7

from app.core.database import callback_session_factory, set_session_tenant_context

_SUPPORTED_CHANGES = {
    "add_external_contact",
    "add_half_external_contact",
    "del_external_contact",
    "del_follow_user",
}


def _map_callback_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        return HTTPException(status_code=503, detail="Enterprise WeChat callback authority unavailable")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="Enterprise WeChat callback subject not found")
    if sqlstate in {"22023", "23514", "23505"}:
        return HTTPException(status_code=409, detail="Enterprise WeChat callback conflict")
    if sqlstate == "55P03":
        return HTTPException(
            status_code=409,
            detail="Enterprise WeChat callback is busy",
            headers={"Retry-After": "1"},
        )
    return None


def _bounded_optional_text(event: dict, *names: str, max_length: int = 1024) -> str | None:
    value = next((event.get(name) for name in names if event.get(name) not in (None, "")), None)
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > max_length:
        raise HTTPException(status_code=422, detail="Invalid Enterprise WeChat callback event")
    return normalized


async def apply_verified_wecom_contact_event(
    *,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    event: dict,
) -> dict:
    """Apply one signed, decrypted official callback without callback-role table DML."""

    event_type = _bounded_optional_text(event, "Event", "event", max_length=100)
    if event_type != "change_external_contact":
        return {"status": "ignored", "reason": "unsupported_event"}
    change_type = _bounded_optional_text(event, "ChangeType", "change_type", max_length=100)
    if change_type not in _SUPPORTED_CHANGES:
        return {"status": "ignored", "reason": "unsupported_change_type"}
    external_userid = _bounded_optional_text(event, "ExternalUserID", "external_userid", max_length=120)
    if external_userid is None:
        return {"status": "ignored", "reason": "missing_external_userid"}
    user_id = _bounded_optional_text(event, "UserID", "user_id", max_length=120)
    if user_id is None:
        raise HTTPException(status_code=422, detail="Invalid Enterprise WeChat callback event")
    try:
        create_time = int(event.get("CreateTime") or event.get("create_time"))
        event_sequence = int(event.get("Sequence") or event.get("sequence") or 0)
        if create_time < 0 or event_sequence < 0:
            raise ValueError
        event_time = datetime.fromtimestamp(create_time, UTC)
    except (TypeError, ValueError, OverflowError):
        return {"status": "ignored", "reason": "invalid_event_order"}

    canonical_event = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload_digest = hashlib.sha256(canonical_event.encode()).hexdigest()
    params = {
        "tenant_id": tenant_id,
        "receipt_id": uuid7(),
        "connector_id": connector_id,
        "change_type": change_type,
        "external_userid": external_userid,
        "user_id": user_id,
        "state": _bounded_optional_text(event, "State", "state", max_length=128),
        "unionid": _bounded_optional_text(event, "UnionID", "unionid", max_length=120),
        "event_time": event_time,
        "event_sequence": event_sequence,
        "payload_digest": payload_digest,
    }
    try:
        async with callback_session_factory() as callback_db:
            await set_session_tenant_context(callback_db, tenant_id)
            row = (
                (
                    await callback_db.execute(
                        text(
                            "SELECT * FROM public.apply_verified_wecom_contact_event("
                            ":tenant_id,:receipt_id,:connector_id,:change_type,:external_userid,:user_id,"
                            ":state,:unionid,:event_time,:event_sequence,:payload_digest)"
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            await callback_db.commit()
    except DBAPIError as exc:
        mapped = _map_callback_error(exc)
        if mapped is None:
            raise
        raise mapped from exc

    result = dict(row)
    if result.get("replayed"):
        result["status"] = "duplicate"
    elif str(result.get("outcome", "")).startswith("ignored"):
        result["status"] = "ignored"
        if result.get("outcome") == "ignored_stale":
            result["reason"] = "non_newer_event"
    else:
        result["status"] = "recorded"
    return result
