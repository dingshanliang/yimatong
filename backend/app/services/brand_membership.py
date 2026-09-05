"""Brand membership authority separate from a consumer profile or channel identity."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

import jwt
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import _session_uses_postgresql
from app.models.consent import (
    ConsentRecord,
    ConsentStatus,
    ConsumerConsentAction,
    ConsumerConsentPolicyCurrent,
)
from app.models.member import (
    BrandMembership,
    BrandMembershipEvent,
    BrandMembershipProfileLink,
    ConsumerProfile,
    MemberIdentityCredential,
)
from app.models.repurchase_coupon import MemberCoupon
from app.utils import utcnow
from app.utils.crypto import (
    decrypt_member_identity_subject,
    encrypt_member_identity_subject,
    hash_member_identity_subject,
)

MEMBERSHIP_CONSENT_PURPOSE = "brand_membership"


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _raise_authority_error(
    exc: DBAPIError,
    *,
    denied: str,
    conflict: str,
    missing: str | None = None,
) -> None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        raise HTTPException(status_code=403, detail=denied) from exc
    if sqlstate == "23503" and missing is not None:
        raise HTTPException(status_code=401, detail=missing) from exc
    if sqlstate in {"22023", "23505", "23514"}:
        raise HTTPException(status_code=409, detail=conflict) from exc
    if sqlstate == "55P03":
        raise HTTPException(status_code=409, detail="membership_authority_busy", headers={"Retry-After": "1"}) from exc
    raise exc


def _serialize(membership: BrandMembership, consumer_id: uuid.UUID, *, replayed: bool = False) -> dict:
    return {
        "membership_id": membership.id,
        "membership_number": membership.membership_number,
        "consumer_id": consumer_id,
        "status": membership.status,
        "joined_at": membership.joined_at,
        "replayed": replayed,
    }


async def _membership_for_profile(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID
) -> tuple[BrandMembership, BrandMembershipProfileLink] | None:
    row = (
        await db.execute(
            select(BrandMembership, BrandMembershipProfileLink)
            .join(
                BrandMembershipProfileLink,
                (BrandMembershipProfileLink.tenant_id == BrandMembership.tenant_id)
                & (BrandMembershipProfileLink.membership_id == BrandMembership.id),
            )
            .where(
                BrandMembership.tenant_id == tenant_id,
                BrandMembership.status == "active",
                BrandMembershipProfileLink.tenant_id == tenant_id,
                BrandMembershipProfileLink.consumer_profile_id == consumer_id,
            )
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def get_brand_membership_for_profile(
    db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID
) -> dict | None:
    found = await _membership_for_profile(db, tenant_id, consumer_id)
    return _serialize(found[0], consumer_id) if found else None


async def get_membership_by_verified_identity(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    subject: str,
) -> tuple[BrandMembership, MemberIdentityCredential] | None:
    """Resolve an active member from a provider-verified subject without exposing that subject."""

    subject_hash = hash_member_identity_subject(tenant_id, credential_type, issuer, subject)
    row = (
        await db.execute(
            select(BrandMembership, MemberIdentityCredential)
            .join(
                MemberIdentityCredential,
                (MemberIdentityCredential.tenant_id == BrandMembership.tenant_id)
                & (MemberIdentityCredential.membership_id == BrandMembership.id),
            )
            .where(
                BrandMembership.tenant_id == tenant_id,
                BrandMembership.status == "active",
                MemberIdentityCredential.tenant_id == tenant_id,
                MemberIdentityCredential.credential_type == credential_type,
                MemberIdentityCredential.issuer == issuer,
                MemberIdentityCredential.subject_hash == subject_hash,
                MemberIdentityCredential.revoked_at.is_(None),
            )
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def join_brand_membership(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
    idempotency_key: str,
) -> dict:
    """Create an explicit membership only from the exact consent and scan authority."""

    request_hash = _payload_hash(
        {
            "consent_id": consent_id,
            "scan_event_id": scan_event_id,
            "scan_time": scan_time,
            "public_id": public_id,
            "visitor_id": visitor_id,
            "token_consumer_id": token_consumer_id,
        }
    )
    existing_event = await db.scalar(
        select(BrandMembershipEvent).where(
            BrandMembershipEvent.tenant_id == tenant_id,
            BrandMembershipEvent.event_type == "joined",
            BrandMembershipEvent.idempotency_key == idempotency_key,
        )
    )
    if existing_event is not None:
        if existing_event.payload_hash != request_hash:
            raise HTTPException(status_code=409, detail="membership_request_conflict")
        membership = await db.scalar(
            select(BrandMembership).where(
                BrandMembership.tenant_id == tenant_id,
                BrandMembership.id == existing_event.membership_id,
            )
        )
        primary = await db.scalar(
            select(BrandMembershipProfileLink).where(
                BrandMembershipProfileLink.tenant_id == tenant_id,
                BrandMembershipProfileLink.membership_id == existing_event.membership_id,
                BrandMembershipProfileLink.is_primary.is_(True),
            )
        )
        if membership is None or primary is None:
            raise HTTPException(status_code=409, detail="membership_receipt_invalid")
        return _serialize(membership, primary.consumer_profile_id, replayed=True)

    consent = await db.scalar(
        select(ConsentRecord).where(
            ConsentRecord.tenant_id == tenant_id,
            ConsentRecord.id == consent_id,
        )
    )
    current_policy = (
        await db.scalar(
            select(ConsumerConsentPolicyCurrent).where(
                ConsumerConsentPolicyCurrent.tenant_id == tenant_id,
                ConsumerConsentPolicyCurrent.purpose == MEMBERSHIP_CONSENT_PURPOSE,
                ConsumerConsentPolicyCurrent.policy_id == consent.policy_id if consent is not None else False,
            )
        )
        if consent is not None
        else None
    )
    grant_receipt = (
        await db.scalar(
            select(ConsumerConsentAction).where(
                ConsumerConsentAction.tenant_id == tenant_id,
                ConsumerConsentAction.consent_id == consent_id,
                ConsumerConsentAction.action == "grant",
                ConsumerConsentAction.result_status == "granted",
            )
        )
        if consent is not None
        else None
    )
    if (
        consent is None
        or consent.status != ConsentStatus.granted
        or consent.purpose != MEMBERSHIP_CONSENT_PURPOSE
        or consent.authority_version != 1
        or current_policy is None
        or grant_receipt is None
        or consent.scan_event_id != scan_event_id
        or consent.scan_event_time != scan_time
        or consent.public_id != public_id
    ):
        raise HTTPException(status_code=403, detail="membership_consent_required")
    if consent.consumer_id is not None and token_consumer_id is not None and consent.consumer_id != token_consumer_id:
        raise HTTPException(status_code=403, detail="membership_subject_conflict")

    consumer_id = token_consumer_id or consent.consumer_id or uuid7()
    if _session_uses_postgresql(db):
        membership_id = uuid7()
        try:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM public.create_brand_membership_authority("
                            ":tenant_id,:consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,"
                            ":consumer_id,:existing_consumer,:membership_id,:membership_number,:link_id,"
                            ":event_id,:idempotency_key,:payload_hash)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "consent_id": consent_id,
                            "scan_event_id": scan_event_id,
                            "scan_time": scan_time,
                            "public_id": public_id,
                            "visitor_id": visitor_id,
                            "consumer_id": consumer_id,
                            "existing_consumer": token_consumer_id is not None or consent.consumer_id is not None,
                            "membership_id": membership_id,
                            "membership_number": f"MBR-{membership_id.hex[:12].upper()}",
                            "link_id": uuid7(),
                            "event_id": uuid7(),
                            "idempotency_key": idempotency_key,
                            "payload_hash": request_hash,
                        },
                    )
                )
                .mappings()
                .one()
            )
        except DBAPIError as exc:
            _raise_authority_error(
                exc,
                denied="membership_consent_required",
                conflict="membership_request_conflict",
                missing="membership_subject_not_found",
            )
        return dict(row)

    profile = await db.scalar(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.id == consumer_id,
        )
    )
    if profile is None:
        if token_consumer_id is not None or consent.consumer_id is not None:
            raise HTTPException(status_code=403, detail="membership_subject_not_found")
        profile = ConsumerProfile(id=consumer_id, tenant_id=tenant_id)
        db.add(profile)
        await db.flush()

    found = await _membership_for_profile(db, tenant_id, consumer_id)
    if found is not None:
        return _serialize(found[0], consumer_id, replayed=True)

    membership_id = uuid7()
    membership = BrandMembership(
        id=membership_id,
        tenant_id=tenant_id,
        membership_number=f"MBR-{membership_id.hex[:12].upper()}",
        status="active",
        join_consent_id=consent_id,
    )
    db.add(membership)
    await db.flush()
    db.add(
        BrandMembershipProfileLink(
            id=uuid7(),
            tenant_id=tenant_id,
            membership_id=membership_id,
            consumer_profile_id=consumer_id,
            is_primary=True,
            link_reason="explicit_join",
            verification_receipt_hash=request_hash,
        )
    )
    db.add(
        BrandMembershipEvent(
            id=uuid7(),
            tenant_id=tenant_id,
            membership_id=membership_id,
            event_type="joined",
            idempotency_key=idempotency_key,
            payload_hash=request_hash,
        )
    )
    consent.consumer_id = consumer_id
    await db.flush()
    await db.refresh(membership)
    return _serialize(membership, consumer_id)


async def bind_verified_member_identity(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    subject: str,
    verification_receipt_hash: str,
    idempotency_key: str,
) -> MemberIdentityCredential:
    """Persist a provider-verified credential; callers must verify the provider before calling."""

    membership = await db.scalar(
        select(BrandMembership).where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.id == membership_id,
            BrandMembership.status == "active",
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="membership_not_found")
    subject_hash = hash_member_identity_subject(tenant_id, credential_type, issuer, subject)
    existing = await db.scalar(
        select(MemberIdentityCredential).where(
            MemberIdentityCredential.tenant_id == tenant_id,
            MemberIdentityCredential.credential_type == credential_type,
            MemberIdentityCredential.issuer == issuer,
            MemberIdentityCredential.subject_hash == subject_hash,
            MemberIdentityCredential.revoked_at.is_(None),
        )
    )
    if existing is not None:
        if existing.membership_id != membership_id:
            raise HTTPException(status_code=409, detail="membership_identity_conflict")
        return existing
    if len(verification_receipt_hash) != 64 or any(
        character not in "0123456789abcdef" for character in verification_receipt_hash
    ):
        raise HTTPException(status_code=409, detail="member_identity_verification_receipt_required")
    ciphertext, nonce, key_id = encrypt_member_identity_subject(
        tenant_id, membership_id, credential_type, issuer, subject
    )
    payload_hash = _payload_hash(
        {
            "credential_type": credential_type,
            "issuer": issuer,
            "subject_hash": subject_hash,
            "verification_receipt_hash": verification_receipt_hash,
        }
    )
    credential_id = uuid7()
    if _session_uses_postgresql(db):
        try:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM public.bind_brand_member_identity_authority("
                            ":tenant_id,:membership_id,:credential_id,:credential_type,:issuer,:subject_hash,"
                            ":subject_ciphertext,:subject_nonce,:subject_key_id,:verification_receipt_hash,"
                            ":event_id,:idempotency_key,:payload_hash)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "membership_id": membership_id,
                            "credential_id": credential_id,
                            "credential_type": credential_type,
                            "issuer": issuer,
                            "subject_hash": subject_hash,
                            "subject_ciphertext": ciphertext,
                            "subject_nonce": nonce,
                            "subject_key_id": key_id,
                            "verification_receipt_hash": verification_receipt_hash,
                            "event_id": uuid7(),
                            "idempotency_key": idempotency_key,
                            "payload_hash": payload_hash,
                        },
                    )
                )
                .mappings()
                .one()
            )
        except DBAPIError as exc:
            _raise_authority_error(
                exc,
                denied="member_identity_authority_denied",
                conflict="membership_identity_conflict",
                missing="membership_not_found",
            )
        credential = await db.scalar(
            select(MemberIdentityCredential).where(
                MemberIdentityCredential.tenant_id == tenant_id,
                MemberIdentityCredential.id == row["credential_id"],
            )
        )
        if credential is None:
            raise HTTPException(status_code=409, detail="membership_identity_receipt_invalid")
        return credential

    credential = MemberIdentityCredential(
        id=credential_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        credential_type=credential_type,
        issuer=issuer,
        subject_hash=subject_hash,
        subject_ciphertext=ciphertext,
        subject_nonce=nonce,
        subject_key_id=key_id,
        verification_receipt_hash=verification_receipt_hash,
    )
    db.add(credential)
    db.add(
        BrandMembershipEvent(
            id=uuid7(),
            tenant_id=tenant_id,
            membership_id=membership_id,
            event_type="identity_bound",
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
    )
    await db.flush()
    return credential


def issue_member_recovery_token(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    credential_id: uuid.UUID,
    *,
    expires_in: int = 300,
) -> str:
    if expires_in <= 0 or expires_in > 300:
        raise ValueError("member recovery token lifetime must be within five minutes")
    return jwt.encode(
        {
            "type": "member_recovery",
            "tenant_id": str(tenant_id),
            "membership_id": str(membership_id),
            "credential_id": str(credential_id),
            "jti": str(uuid.uuid4()),
            "exp": int(time.time()) + expires_in,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _decode_member_recovery_token(recovery_token: str, tenant_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, str]:
    try:
        payload = jwt.decode(recovery_token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "member_recovery" or uuid.UUID(payload["tenant_id"]) != tenant_id:
            raise ValueError
        return (
            uuid.UUID(payload["membership_id"]),
            uuid.UUID(payload["credential_id"]),
            str(uuid.UUID(payload["jti"])),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid_member_recovery") from exc


async def _verified_recovery_authority(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    recovery_token: str,
) -> tuple[BrandMembership, MemberIdentityCredential, str]:
    membership_id, credential_id, jti = _decode_member_recovery_token(recovery_token, tenant_id)
    credential = await db.scalar(
        select(MemberIdentityCredential).where(
            MemberIdentityCredential.tenant_id == tenant_id,
            MemberIdentityCredential.id == credential_id,
            MemberIdentityCredential.membership_id == membership_id,
            MemberIdentityCredential.revoked_at.is_(None),
        )
    )
    membership = await db.scalar(
        select(BrandMembership).where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.id == membership_id,
            BrandMembership.status == "active",
        )
    )
    if credential is None or membership is None:
        raise HTTPException(status_code=401, detail="invalid_member_recovery")
    return membership, credential, jti


async def recover_brand_membership(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    current_consumer_id: uuid.UUID,
    recovery_token: str,
    idempotency_key: str,
) -> dict:
    target, credential, recovery_jti = await _verified_recovery_authority(
        db, tenant_id=tenant_id, recovery_token=recovery_token
    )
    token_use_key = f"recovery-token:{recovery_jti}"
    existing_event = await db.scalar(
        select(BrandMembershipEvent).where(
            BrandMembershipEvent.tenant_id == tenant_id,
            BrandMembershipEvent.idempotency_key == token_use_key,
        )
    )
    event_hash = _payload_hash({"target": target.id, "credential": credential.id, "consumer": current_consumer_id})
    if existing_event is not None:
        if existing_event.event_type != "recovered" or existing_event.payload_hash != event_hash:
            raise HTTPException(status_code=409, detail="member_recovery_token_already_used")
        return _serialize(target, current_consumer_id, replayed=True)
    current = await _membership_for_profile(db, tenant_id, current_consumer_id)
    if current is not None and current[0].id != target.id:
        raise HTTPException(status_code=409, detail="membership_merge_requires_two_verified_credentials")
    if _session_uses_postgresql(db):
        try:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM public.recover_brand_membership_authority("
                            ":tenant_id,:membership_id,:credential_id,:consumer_id,:link_id,:event_id,"
                            ":token_use_key,:payload_hash)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "membership_id": target.id,
                            "credential_id": credential.id,
                            "consumer_id": current_consumer_id,
                            "link_id": uuid7(),
                            "event_id": uuid7(),
                            "token_use_key": token_use_key,
                            "payload_hash": event_hash,
                        },
                    )
                )
                .mappings()
                .one()
            )
        except DBAPIError as exc:
            _raise_authority_error(
                exc,
                denied="member_recovery_authority_denied",
                conflict="member_recovery_token_already_used",
                missing="invalid_member_recovery",
            )
        return dict(row)
    if current is None:
        db.add(
            BrandMembershipProfileLink(
                id=uuid7(),
                tenant_id=tenant_id,
                membership_id=target.id,
                consumer_profile_id=current_consumer_id,
                is_primary=False,
                link_reason="verified_recovery",
                verification_receipt_hash=event_hash,
            )
        )
    db.add(
        BrandMembershipEvent(
            id=uuid7(),
            tenant_id=tenant_id,
            membership_id=target.id,
            event_type="recovered",
            idempotency_key=token_use_key,
            payload_hash=event_hash,
            occurred_at=utcnow(),
        )
    )
    await db.flush()
    return _serialize(target, current_consumer_id)


async def merge_brand_memberships(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    current_consumer_id: uuid.UUID,
    current_recovery_token: str,
    target_recovery_token: str,
    idempotency_key: str,
) -> dict:
    """Merge only after independently proving both conflicting memberships."""

    current = await _membership_for_profile(db, tenant_id, current_consumer_id)
    if current is None:
        raise HTTPException(status_code=409, detail="current_membership_required")
    source_id, source_credential_id, source_jti = _decode_member_recovery_token(current_recovery_token, tenant_id)
    target_id, target_credential_id, target_jti = _decode_member_recovery_token(target_recovery_token, tenant_id)
    source_key = f"recovery-token:{source_jti}"
    target_key = f"recovery-token:{target_jti}"
    event_hash = _payload_hash(
        {
            "source": source_id,
            "source_credential": source_credential_id,
            "target": target_id,
            "target_credential": target_credential_id,
            "consumer": current_consumer_id,
        }
    )
    prior_events = (
        await db.scalars(
            select(BrandMembershipEvent).where(
                BrandMembershipEvent.tenant_id == tenant_id,
                BrandMembershipEvent.idempotency_key.in_([source_key, target_key]),
            )
        )
    ).all()
    if prior_events:
        if (
            len(prior_events) == 2
            and {event.event_type for event in prior_events} == {"merged_source", "merged_target"}
            and all(event.payload_hash == event_hash for event in prior_events)
        ):
            target = await db.scalar(
                select(BrandMembership).where(
                    BrandMembership.tenant_id == tenant_id,
                    BrandMembership.id == target_id,
                    BrandMembership.status == "active",
                )
            )
            if target is None:
                raise HTTPException(status_code=409, detail="membership_receipt_invalid")
            return _serialize(target, current_consumer_id, replayed=True)
        raise HTTPException(status_code=409, detail="member_recovery_token_already_used")

    source, source_credential, source_jti = await _verified_recovery_authority(
        db, tenant_id=tenant_id, recovery_token=current_recovery_token
    )
    target, target_credential, target_jti = await _verified_recovery_authority(
        db, tenant_id=tenant_id, recovery_token=target_recovery_token
    )
    if source.id != current[0].id:
        raise HTTPException(status_code=403, detail="current_membership_proof_mismatch")
    if source.id == target.id or source_credential.id == target_credential.id or source_jti == target_jti:
        raise HTTPException(status_code=409, detail="two_distinct_membership_proofs_required")
    membership_query = (
        select(BrandMembership)
        .where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.id.in_([source.id, target.id]),
        )
        .order_by(BrandMembership.id)
        .execution_options(populate_existing=True)
    )
    if not _session_uses_postgresql(db):
        membership_query = membership_query.with_for_update()
    locked_memberships = (await db.scalars(membership_query)).all()
    locked_by_id = {membership.id: membership for membership in locked_memberships}
    source = locked_by_id.get(source.id)
    target = locked_by_id.get(target.id)
    if source is None or target is None or source.status != "active" or target.status != "active":
        raise HTTPException(status_code=409, detail="membership_merge_state_changed")
    if await db.scalar(
        select(MemberCoupon.id).where(
            MemberCoupon.tenant_id == tenant_id,
            MemberCoupon.membership_id.in_([source.id, target.id]),
            MemberCoupon.status == "reserved",
        )
    ):
        raise HTTPException(status_code=409, detail="membership_merge_blocked_by_reserved_coupon")

    source_links = (
        await db.scalars(
            select(BrandMembershipProfileLink).where(
                BrandMembershipProfileLink.tenant_id == tenant_id,
                BrandMembershipProfileLink.membership_id == source.id,
            )
        )
    ).all()
    source_credentials = (
        await db.scalars(
            select(MemberIdentityCredential).where(
                MemberIdentityCredential.tenant_id == tenant_id,
                MemberIdentityCredential.membership_id == source.id,
                MemberIdentityCredential.revoked_at.is_(None),
            )
        )
    ).all()
    reencrypted_credentials: dict[str, dict[str, str]] = {}
    for credential in source_credentials:
        subject = decrypt_member_identity_subject(
            tenant_id,
            source.id,
            credential.credential_type,
            credential.issuer,
            credential.subject_ciphertext,
            credential.subject_nonce,
            credential.subject_key_id,
        )
        ciphertext, nonce, key_id = encrypt_member_identity_subject(
            tenant_id,
            target.id,
            credential.credential_type,
            credential.issuer,
            subject,
        )
        reencrypted_credentials[str(credential.id)] = {
            "ciphertext_hex": ciphertext.hex(),
            "nonce_hex": nonce.hex(),
            "key_id": key_id,
        }
    if _session_uses_postgresql(db):
        try:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM public.merge_brand_memberships_authority("
                            ":tenant_id,:source_id,:target_id,:consumer_id,:source_credential_id,"
                            ":target_credential_id,:source_token_key,:target_token_key,:source_event_id,"
                            ":target_event_id,:payload_hash,CAST(:reencrypted_credentials AS jsonb))"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "source_id": source.id,
                            "target_id": target.id,
                            "consumer_id": current_consumer_id,
                            "source_credential_id": source_credential.id,
                            "target_credential_id": target_credential.id,
                            "source_token_key": source_key,
                            "target_token_key": target_key,
                            "source_event_id": uuid7(),
                            "target_event_id": uuid7(),
                            "payload_hash": event_hash,
                            "reencrypted_credentials": json.dumps(reencrypted_credentials, separators=(",", ":")),
                        },
                    )
                )
                .mappings()
                .one()
            )
        except DBAPIError as exc:
            _raise_authority_error(
                exc,
                denied="member_merge_authority_denied",
                conflict="membership_merge_state_changed",
                missing="invalid_member_recovery",
            )
        return dict(row)

    for link in source_links:
        link.membership_id = target.id
        link.is_primary = False
        link.link_reason = "verified_merge"
        link.verification_receipt_hash = event_hash
    for credential in source_credentials:
        envelope = reencrypted_credentials[str(credential.id)]
        credential.membership_id = target.id
        credential.subject_ciphertext = bytes.fromhex(envelope["ciphertext_hex"])
        credential.subject_nonce = bytes.fromhex(envelope["nonce_hex"])
        credential.subject_key_id = envelope["key_id"]
    source_coupons = (
        await db.scalars(
            select(MemberCoupon).where(
                MemberCoupon.tenant_id == tenant_id,
                MemberCoupon.membership_id == source.id,
            )
        )
    ).all()
    for coupon in source_coupons:
        coupon.membership_id = target.id
    source.status = "merged"
    source.merged_into_id = target.id
    source.updated_at = utcnow()
    db.add_all(
        [
            BrandMembershipEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                membership_id=source.id,
                event_type="merged_source",
                idempotency_key=source_key,
                payload_hash=event_hash,
            ),
            BrandMembershipEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                membership_id=target.id,
                event_type="merged_target",
                idempotency_key=target_key,
                payload_hash=event_hash,
            ),
        ]
    )
    await db.flush()
    return _serialize(target, current_consumer_id)
