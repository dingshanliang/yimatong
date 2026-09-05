from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, set_session_tenant_context
from app.models.member import BrandMembership, BrandMembershipProfileLink, ConsumerProfile
from app.models.privacy_governance import PrivacyRightsEvent, PrivacyRightsRequest
from app.utils.crypto import CryptoError, decrypt_bytes, decrypt_consumer_phone, encrypt_bytes


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))


def _session_payload(actor_id: uuid.UUID, auth_session_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "actor_account_id": str(actor_id),
        "auth_session_id": str(auth_session_id),
        "event_id": str(uuid7()),
    }


def _db_error(exc: DBAPIError) -> HTTPException:
    code = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if code == "42501":
        return HTTPException(status_code=403, detail="privacy_authority_denied")
    if code == "23503":
        return HTTPException(status_code=404, detail="privacy_resource_not_found")
    return HTTPException(status_code=409, detail="privacy_governance_conflict")


async def create_consumer_privacy_request(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    membership_id: uuid.UUID,
    request_type: str,
    reason: str,
    evidence: dict,
) -> uuid.UUID:
    request_id = uuid7()
    payload = {
        "request_id": str(request_id),
        "event_id": str(uuid7()),
        "request_number": f"PIR-{datetime.now(UTC):%Y%m%d}-{request_id.hex[-16:].upper()}",
        "consumer_id": str(consumer_id),
        "membership_id": str(membership_id),
        "request_type": request_type,
        "reason": reason,
        "evidence": evidence,
    }
    if _session_uses_postgresql(db):
        try:
            return uuid.UUID(
                str(
                    await db.scalar(
                        text(
                            "SELECT public.create_consumer_privacy_request_authority("
                            ":tenant_id,CAST(:payload AS jsonb))"
                        ),
                        {"tenant_id": tenant_id, "payload": _json(payload)},
                    )
                )
            )
        except DBAPIError as exc:
            raise _db_error(exc) from exc

    existing = await db.scalar(
        select(PrivacyRightsRequest).where(
            PrivacyRightsRequest.tenant_id == tenant_id,
            PrivacyRightsRequest.consumer_id == consumer_id,
            PrivacyRightsRequest.request_type == request_type,
            PrivacyRightsRequest.status.not_in(["completed", "rejected"]),
        )
    )
    if existing is not None:
        return existing.id
    item = PrivacyRightsRequest(
        id=request_id,
        tenant_id=tenant_id,
        request_number=payload["request_number"],
        consumer_id=consumer_id,
        membership_id=membership_id,
        request_type=request_type,
        due_at=datetime.now(UTC) + timedelta(days=15),
        evidence=evidence,
    )
    db.add(item)
    digest = hashlib.sha256(_json(payload).encode()).hexdigest()
    db.add(
        PrivacyRightsEvent(
            id=uuid.UUID(payload["event_id"]),
            tenant_id=tenant_id,
            request_id=request_id,
            action="submitted",
            from_status=None,
            to_status="submitted",
            reason=reason,
            evidence=evidence,
            actor_kind="consumer",
            actor_ref=str(membership_id),
            previous_digest="0" * 64,
            event_digest=digest,
        )
    )
    await db.flush()
    return request_id


async def list_privacy_requests(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    await set_session_tenant_context(db, tenant_id)
    rows = (
        await db.scalars(
            select(PrivacyRightsRequest)
            .where(PrivacyRightsRequest.tenant_id == tenant_id)
            .order_by(PrivacyRightsRequest.completed_at.is_not(None), PrivacyRightsRequest.due_at)
        )
    ).all()
    now = datetime.now(UTC)
    return [
        {
            "id": item.id,
            "request_number": item.request_number,
            "request_type": item.request_type,
            "status": item.status,
            "consumer_id": item.consumer_id,
            "membership_id": item.membership_id,
            "owner_account_id": item.owner_account_id,
            "due_at": item.due_at,
            "overdue": item.completed_at is None and item.due_at.replace(tzinfo=item.due_at.tzinfo or UTC) < now,
            "outcome": item.outcome,
            "evidence": item.evidence,
            "created_at": item.created_at,
        }
        for item in rows
    ]


async def mutate_privacy_request(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: dict[str, Any],
) -> uuid.UUID:
    prepared = _session_payload(actor_id, auth_session_id, {**payload, "request_id": str(request_id)})
    if not _session_uses_postgresql(db):
        raise HTTPException(status_code=503, detail="privacy_authority_requires_postgresql")
    try:
        return uuid.UUID(
            str(
                await db.scalar(
                    text("SELECT public.mutate_privacy_rights_authority(:tenant_id,CAST(:payload AS jsonb))"),
                    {"tenant_id": tenant_id, "payload": _json(prepared)},
                )
            )
        )
    except DBAPIError as exc:
        raise _db_error(exc) from exc


async def _record_pii_access(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    consumer_id: uuid.UUID,
    outcome: str,
    reason: str,
    ticket_ref: str | None,
    request_trace_id: str | None,
) -> None:
    payload = _session_payload(
        actor_id,
        auth_session_id,
        {
            "consumer_id": str(consumer_id),
            "fields": ["phone"],
            "outcome": outcome,
            "reason": reason,
            "ticket_ref": ticket_ref,
            "request_trace_id": request_trace_id,
        },
    )
    try:
        await db.scalar(
            text("SELECT public.record_member_pii_access_authority(:tenant_id,CAST(:payload AS jsonb))"),
            {"tenant_id": tenant_id, "payload": _json(payload)},
        )
    except DBAPIError as exc:
        raise _db_error(exc) from exc


async def reveal_consumer_phone(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    consumer_id: uuid.UUID,
    reason: str,
    ticket_ref: str | None,
    request_trace_id: str | None,
) -> str | None:
    if not _session_uses_postgresql(db):
        raise HTTPException(status_code=503, detail="pii_reveal_requires_postgresql")
    await _record_pii_access(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=auth_session_id,
        consumer_id=consumer_id,
        outcome="authorized",
        reason=reason,
        ticket_ref=ticket_ref,
        request_trace_id=request_trace_id,
    )
    profile = await db.scalar(
        select(ConsumerProfile).where(ConsumerProfile.tenant_id == tenant_id, ConsumerProfile.id == consumer_id)
    )
    outcome = "failed"
    phone = None
    try:
        if (
            profile
            and not profile.lead_contact_suppressed
            and profile.phone_ciphertext
            and profile.phone_nonce
            and profile.phone_key_id
        ):
            phone = decrypt_consumer_phone(
                tenant_id,
                consumer_id,
                profile.phone_ciphertext,
                profile.phone_nonce,
                profile.phone_key_id,
            )
        outcome = "succeeded"
        return phone
    except CryptoError as exc:
        raise HTTPException(status_code=503, detail="pii_key_unavailable") from exc
    finally:
        await _record_pii_access(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            auth_session_id=auth_session_id,
            consumer_id=consumer_id,
            outcome=outcome,
            reason=reason,
            ticket_ref=ticket_ref,
            request_trace_id=request_trace_id,
        )


async def mutate_sensitive_export(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    export_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not _session_uses_postgresql(db):
        raise HTTPException(status_code=503, detail="sensitive_export_requires_postgresql")
    prepared = _session_payload(actor_id, auth_session_id, {**payload, "export_id": str(export_id)})
    try:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM public.mutate_sensitive_member_export_authority("
                        ":tenant_id,CAST(:payload AS jsonb))"
                    ),
                    {"tenant_id": tenant_id, "payload": _json(prepared)},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)
    except DBAPIError as exc:
        raise _db_error(exc) from exc


async def list_sensitive_exports(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    await set_session_tenant_context(db, tenant_id)
    return [
        dict(row)
        for row in (
            await db.execute(
                text(
                    "SELECT * FROM public.sensitive_member_export_summaries "
                    "WHERE tenant_id=:tenant_id ORDER BY created_at DESC LIMIT 100"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
    ]


async def maintain_privacy_retention(db: AsyncSession, tenant_id: uuid.UUID, *, limit: int = 100) -> dict[str, int]:
    """Apply monotonic retention controls after normal operation or a database restore."""
    if not _session_uses_postgresql(db):
        raise HTTPException(status_code=503, detail="privacy_retention_requires_postgresql")
    await set_session_tenant_context(db, tenant_id)
    try:
        restored_controls = int(
            await db.scalar(
                text("SELECT public.reapply_completed_privacy_controls_authority(:tenant_id)"),
                {"tenant_id": tenant_id},
            )
            or 0
        )
        purged_exports = int(
            await db.scalar(
                text("SELECT public.purge_expired_sensitive_exports_authority(:tenant_id,:limit)"),
                {"tenant_id": tenant_id, "limit": limit},
            )
            or 0
        )
    except DBAPIError as exc:
        raise _db_error(exc) from exc
    return {"restored_controls": restored_controls, "purged_exports": purged_exports}


async def prepare_sensitive_export(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    export_id: uuid.UUID,
    reason: str,
) -> str:
    summary = next((item for item in await list_sensitive_exports(db, tenant_id) if item["id"] == export_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="sensitive_export_not_found")
    fields = list(summary["requested_fields"])
    filters = dict(summary["filters"] or {})
    query = (
        select(BrandMembership, ConsumerProfile)
        .join(
            BrandMembershipProfileLink,
            (BrandMembershipProfileLink.tenant_id == BrandMembership.tenant_id)
            & (BrandMembershipProfileLink.membership_id == BrandMembership.id),
        )
        .join(
            ConsumerProfile,
            (ConsumerProfile.tenant_id == BrandMembershipProfileLink.tenant_id)
            & (ConsumerProfile.id == BrandMembershipProfileLink.consumer_profile_id),
        )
        .where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.status == filters.get("membership_status", "active"),
        )
        .order_by(BrandMembership.id, ConsumerProfile.id)
    )
    if filters.get("membership_ids"):
        query = query.where(BrandMembership.id.in_([uuid.UUID(str(item)) for item in filters["membership_ids"]]))
    if filters.get("consumer_ids"):
        query = query.where(ConsumerProfile.id.in_([uuid.UUID(str(item)) for item in filters["consumer_ids"]]))
    if filters.get("joined_from"):
        query = query.where(BrandMembership.joined_at >= datetime.fromisoformat(str(filters["joined_from"])))
    if filters.get("joined_until"):
        query = query.where(BrandMembership.joined_at < datetime.fromisoformat(str(filters["joined_until"])))
    rows = (
        await db.execute(query.limit(10_001))
    ).all()
    if len(rows) > 10_000:
        raise HTTPException(status_code=409, detail="sensitive_export_scope_too_large")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for membership, profile in rows:
        record: dict[str, str] = {}
        if "membership_number" in fields:
            record["membership_number"] = membership.membership_number
        if "nickname" in fields:
            record["nickname"] = "" if profile.lead_contact_suppressed else profile.nickname or ""
        if "phone" in fields:
            try:
                has_phone = (
                    not profile.lead_contact_suppressed
                    and profile.phone_ciphertext
                    and profile.phone_nonce
                    and profile.phone_key_id
                )
                record["phone"] = (
                    decrypt_consumer_phone(
                        tenant_id,
                        profile.id,
                        profile.phone_ciphertext,
                        profile.phone_nonce,
                        profile.phone_key_id,
                    )
                    if has_phone
                    else ""
                )
            except CryptoError as exc:
                raise HTTPException(status_code=503, detail="pii_key_unavailable") from exc
        writer.writerow(record)
    plaintext = stream.getvalue().encode("utf-8-sig")
    aad = b"sensitive-member-export-v1\0" + tenant_id.bytes + export_id.bytes
    ciphertext, nonce, key_id = encrypt_bytes(plaintext, aad=aad)
    token = secrets.token_urlsafe(32)
    await mutate_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=auth_session_id,
        export_id=export_id,
        payload={
            "action": "prepare",
            "reason": reason,
            "row_count": len(rows),
            "artifact_ciphertext": base64.b64encode(ciphertext).decode(),
            "artifact_nonce": base64.b64encode(nonce).decode(),
            "artifact_key_id": key_id,
            "artifact_size_bytes": len(plaintext),
            "checksum_sha256": hashlib.sha256(plaintext).hexdigest(),
            "download_token_digest": hashlib.sha256(token.encode()).hexdigest(),
        },
    )
    return token


async def download_sensitive_export(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    export_id: uuid.UUID,
    token: str,
    reason: str,
) -> tuple[bytes, str]:
    row = await mutate_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=auth_session_id,
        export_id=export_id,
        payload={
            "action": "download",
            "reason": reason,
            "download_token_digest": hashlib.sha256(token.encode()).hexdigest(),
        },
    )
    try:
        plaintext = decrypt_bytes(
            row["artifact_ciphertext"],
            nonce=row["artifact_nonce"],
            key_id=row["artifact_key_id"],
            aad=b"sensitive-member-export-v1\0" + tenant_id.bytes + export_id.bytes,
        )
    except CryptoError as exc:
        raise HTTPException(status_code=503, detail="export_key_unavailable") from exc
    if hashlib.sha256(plaintext).hexdigest() != row["checksum_sha256"]:
        raise HTTPException(status_code=409, detail="sensitive_export_integrity_failure")
    return plaintext, row["file_name"]
