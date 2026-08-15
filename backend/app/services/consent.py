"""消费者同意记录服务"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.consent import (
    ConsentRecord,
    ConsentStatus,
    ConsumerConsentAction,
    ConsumerConsentPolicy,
    ConsumerConsentPolicyCurrent,
)
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.models.visitor import AnonymousVisitor
from app.utils import utcnow


@dataclass(frozen=True)
class ConsumerScanAuthority:
    tenant_id: uuid.UUID
    scan_event_id: uuid.UUID
    scan_time: datetime
    public_id: str
    visitor_id: str
    consumer_id: uuid.UUID | None
    rate_subject: str


def require_consumer_scan_authority(payload: dict[str, Any]) -> ConsumerScanAuthority:
    try:
        tenant_id = uuid.UUID(payload["tenant_id"])
        scan_event_id = uuid.UUID(payload["scan_event_id"])
        scan_time = datetime.fromisoformat(payload["scan_time"])
        public_id = payload["public_id"]
        visitor_id = payload["visitor_id"]
        rate_subject = payload["jti"]
        consumer_id = uuid.UUID(payload["consumer_id"]) if payload.get("consumer_id") else None
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid_scan_authority") from exc
    if scan_time.tzinfo is None or not all(
        isinstance(value, str) and value.strip() for value in (public_id, visitor_id, rate_subject)
    ):
        raise HTTPException(status_code=401, detail="invalid_scan_authority")
    return ConsumerScanAuthority(
        tenant_id=tenant_id,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id=public_id,
        visitor_id=visitor_id,
        consumer_id=consumer_id,
        rate_subject=rate_subject,
    )


def _authority_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    mapping = {
        "42501": (403, "consent_authority_denied"),
        "23503": (404, "consent_authority_not_found"),
        "23514": (409, "consent_authority_invalid"),
        "23505": (409, "consent_request_conflict"),
        "22023": (409, "consent_request_conflict"),
        "55P03": (409, "consent_authority_retry"),
    }
    result = mapping.get(sqlstate)
    return HTTPException(status_code=result[0], detail=result[1]) if result else None


async def _call_authority(db: AsyncSession, statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return (await db.execute(text(statement), params)).mappings().one_or_none()
    except DBAPIError as exc:
        mapped = _authority_error(exc)
        if mapped:
            raise mapped from exc
        raise


def _uses_postgresql(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _payload_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


async def _sqlite_subject(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    consumer_id: uuid.UUID | None,
) -> tuple[str, AnonymousVisitor]:
    event = await db.scalar(
        select(ScanEvent).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.id == scan_event_id,
            ScanEvent.public_id == public_id,
            ScanEvent.visitor_id == visitor_id,
            ScanEvent.is_valid_visit.is_(True),
        )
    )
    visitor = await db.scalar(
        select(AnonymousVisitor).where(
            AnonymousVisitor.tenant_id == tenant_id,
            AnonymousVisitor.visitor_id == visitor_id,
        )
    )
    event_time = None
    if event is not None:
        event_time = (
            event.scan_time.replace(tzinfo=UTC) if event.scan_time.tzinfo is None else event.scan_time.astimezone(UTC)
        )
    if event is None or visitor is None or event_time != scan_time.astimezone(UTC):
        raise HTTPException(status_code=409, detail="consent_authority_invalid")
    if visitor.consumer_id != consumer_id:
        raise HTTPException(status_code=403, detail="consent_authority_denied")
    subject_hash = hashlib.sha256(f"{tenant_id}:{visitor_id}".encode()).hexdigest()
    return subject_hash, visitor


async def get_current_consumer_policy(db: AsyncSession, tenant_id: uuid.UUID, purpose: str) -> dict[str, Any]:
    if not _uses_postgresql(db):
        row = (
            await db.execute(
                select(ConsumerConsentPolicy)
                .join(
                    ConsumerConsentPolicyCurrent,
                    (ConsumerConsentPolicyCurrent.tenant_id == ConsumerConsentPolicy.tenant_id)
                    & (ConsumerConsentPolicyCurrent.policy_id == ConsumerConsentPolicy.id),
                )
                .where(
                    ConsumerConsentPolicy.tenant_id == tenant_id,
                    ConsumerConsentPolicyCurrent.purpose == purpose,
                    ConsumerConsentPolicy.effective_at <= utcnow(),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="consent_policy_not_found")
        return {
            key: getattr(row, key)
            for key in (
                "purpose",
                "consent_type",
                "policy_version",
                "policy_digest",
                "policy_title",
                "policy_content",
                "effective_at",
            )
        } | {"policy_id": row.id}
    row = await _call_authority(
        db,
        "SELECT * FROM public.get_current_consumer_consent_policy(:tenant_id, :purpose)",
        {"tenant_id": tenant_id, "purpose": purpose},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="consent_policy_not_found")
    return dict(row)


async def get_consumer_consent_receipt_status(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
) -> dict[str, Any]:
    if not _uses_postgresql(db):
        subject_hash, _ = await _sqlite_subject(
            db, tenant_id, scan_event_id, scan_time, public_id, visitor_id, token_consumer_id
        )
        record = await db.scalar(
            select(ConsentRecord).where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.id == consent_id,
            )
        )
        if (
            record is None
            or record.authority_version != 1
            or record.visitor_subject_hash != subject_hash
            or record.public_id != public_id
            or (record.consumer_id is not None and record.consumer_id != token_consumer_id)
        ):
            raise HTTPException(status_code=403, detail="consent_authority_denied")
        return {
            "consent_id": record.id,
            "status": record.status,
            "purpose": record.purpose,
            "policy_version": record.policy_version,
            "policy_digest": record.policy_digest,
            "consumer_id": record.consumer_id,
            "granted_at": record.granted_at,
            "withdrawn_at": record.withdrawn_at,
        }
    row = await _call_authority(
        db,
        "SELECT * FROM public.get_consumer_consent_receipt_status("
        ":tenant_id,:consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,:token_consumer_id)",
        {
            "tenant_id": tenant_id,
            "consent_id": consent_id,
            "scan_event_id": scan_event_id,
            "scan_time": scan_time,
            "public_id": public_id,
            "visitor_id": visitor_id,
            "token_consumer_id": token_consumer_id,
        },
    )
    if row is None:
        raise HTTPException(status_code=403, detail="consent_authority_denied")
    return dict(row)


async def grant_consumer_consent_authority(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    purpose: str,
    expected_version: str,
    expected_digest: str,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
    ip_hash: str,
    user_agent: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not _uses_postgresql(db):
        subject_hash, _ = await _sqlite_subject(
            db, tenant_id, scan_event_id, scan_time, public_id, visitor_id, token_consumer_id
        )
        policy = await get_current_consumer_policy(db, tenant_id, purpose)
        if policy["policy_version"] != expected_version or policy["policy_digest"] != expected_digest:
            raise HTTPException(status_code=409, detail="consent_authority_invalid")
        payload_hash = _payload_digest(
            {
                "purpose": purpose,
                "version": expected_version,
                "digest": expected_digest,
                "scan_event_id": scan_event_id,
                "scan_time": scan_time,
                "public_id": public_id,
                "subject": subject_hash,
                "consumer_id": token_consumer_id,
            }
        )
        existing = await db.scalar(
            select(ConsumerConsentAction).where(
                ConsumerConsentAction.tenant_id == tenant_id,
                ConsumerConsentAction.action == "grant",
                ConsumerConsentAction.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.payload_hash != payload_hash:
                raise HTTPException(status_code=409, detail="consent_request_conflict")
            record = await db.get(ConsentRecord, existing.consent_id)
            return {
                "consent_id": record.id,
                "status": record.status,
                "purpose": record.purpose,
                "policy_version": record.policy_version,
                "policy_digest": record.policy_digest,
                "consumer_id": record.consumer_id,
                "granted_at": record.granted_at,
                "replayed": True,
            }
        active_record = await db.scalar(
            select(ConsentRecord).where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.purpose == purpose,
                ConsentRecord.visitor_subject_hash == subject_hash,
                ConsentRecord.policy_id == policy["policy_id"],
                ConsentRecord.authority_version == 1,
                ConsentRecord.status == "granted",
            )
        )
        if active_record is not None:
            if active_record.consumer_id is not None and active_record.consumer_id != token_consumer_id:
                raise HTTPException(status_code=403, detail="consent_authority_denied")
            db.add(
                ConsumerConsentAction(
                    id=uuid7(),
                    tenant_id=tenant_id,
                    consent_id=active_record.id,
                    policy_id=policy["policy_id"],
                    consumer_id=active_record.consumer_id,
                    action="grant",
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    visitor_subject_hash=subject_hash,
                    result_status="granted",
                )
            )
            await db.flush()
            return {
                "consent_id": active_record.id,
                "status": active_record.status,
                "purpose": active_record.purpose,
                "policy_version": active_record.policy_version,
                "policy_digest": active_record.policy_digest,
                "consumer_id": active_record.consumer_id,
                "granted_at": active_record.granted_at,
                "replayed": True,
            }
        consent_id, audit_id = uuid7(), uuid7()
        record = ConsentRecord(
            id=consent_id,
            tenant_id=tenant_id,
            consumer_id=token_consumer_id,
            consent_type=policy["consent_type"],
            status="granted",
            public_id=public_id,
            ip_hash=ip_hash,
            scenario=purpose,
            policy_version=expected_version,
            user_agent=user_agent[:500],
            policy_id=policy["policy_id"],
            purpose=purpose,
            policy_digest=expected_digest,
            visitor_subject_hash=subject_hash,
            scan_event_id=scan_event_id,
            scan_event_time=scan_time,
            idempotency_key=idempotency_key,
            authority_version=1,
        )
        db.add(record)
        db.add(
            ConsumerConsentAction(
                id=audit_id,
                tenant_id=tenant_id,
                consent_id=consent_id,
                policy_id=policy["policy_id"],
                consumer_id=token_consumer_id,
                action="grant",
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                visitor_subject_hash=subject_hash,
                result_status="granted",
            )
        )
        await db.flush()
        return {
            "consent_id": consent_id,
            "status": "granted",
            "purpose": purpose,
            "policy_version": expected_version,
            "policy_digest": expected_digest,
            "consumer_id": token_consumer_id,
            "granted_at": record.granted_at,
            "replayed": False,
        }
    row = await _call_authority(
        db,
        "SELECT * FROM public.grant_consumer_consent(:tenant_id,:consent_id,:audit_id,:purpose,"
        ":expected_version,:expected_digest,:scan_event_id,:scan_time,:public_id,:visitor_id,"
        ":token_consumer_id,:ip_hash,:user_agent,:idempotency_key)",
        {
            "tenant_id": tenant_id,
            "consent_id": uuid7(),
            "audit_id": uuid7(),
            "purpose": purpose,
            "expected_version": expected_version,
            "expected_digest": expected_digest,
            "scan_event_id": scan_event_id,
            "scan_time": scan_time,
            "public_id": public_id,
            "visitor_id": visitor_id,
            "token_consumer_id": token_consumer_id,
            "ip_hash": ip_hash,
            "user_agent": user_agent[:500],
            "idempotency_key": idempotency_key,
        },
    )
    if row is None:
        raise HTTPException(status_code=409, detail="consent_authority_invalid")
    return dict(row)


async def withdraw_consumer_consent_authority(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
    idempotency_key: str,
) -> dict[str, Any]:
    if not _uses_postgresql(db):
        subject_hash, _ = await _sqlite_subject(
            db, tenant_id, scan_event_id, scan_time, public_id, visitor_id, token_consumer_id
        )
        record = await db.scalar(
            select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.id == consent_id)
        )
        if record is None:
            raise HTTPException(status_code=404, detail="consent_authority_not_found")
        if (
            record.authority_version != 1
            or record.visitor_subject_hash != subject_hash
            or record.public_id != public_id
        ):
            raise HTTPException(status_code=403, detail="consent_authority_denied")
        if record.consumer_id is not None and record.consumer_id != token_consumer_id:
            raise HTTPException(status_code=403, detail="consent_authority_denied")
        payload_hash = _payload_digest({"consent_id": consent_id, "subject": subject_hash})
        existing = await db.scalar(
            select(ConsumerConsentAction).where(
                ConsumerConsentAction.tenant_id == tenant_id,
                ConsumerConsentAction.action == "withdraw",
                ConsumerConsentAction.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.payload_hash != payload_hash:
                raise HTTPException(status_code=409, detail="consent_request_conflict")
            profile = None
            if record.consumer_id is not None:
                profile = await db.scalar(
                    select(ConsumerProfile).where(
                        ConsumerProfile.tenant_id == tenant_id,
                        ConsumerProfile.id == record.consumer_id,
                    )
                )
            return {
                "consent_id": record.id,
                "status": record.status,
                "consumer_id": record.consumer_id,
                "contact_suppressed": bool(profile and profile.lead_contact_suppressed),
                "withdrawn_at": record.withdrawn_at,
                "replayed": True,
            }
        if record.status == "withdrawn":
            raise HTTPException(status_code=409, detail="consent_authority_invalid")
        record.status, record.withdrawn_at = "withdrawn", utcnow()
        suppressed = False
        if record.purpose == "lead_capture" and record.consumer_id:
            profile = await db.scalar(
                select(ConsumerProfile).where(
                    ConsumerProfile.tenant_id == tenant_id, ConsumerProfile.id == record.consumer_id
                )
            )
            if profile and profile.lead_consent_id == record.id:
                profile.lead_contact_suppressed = True
                profile.phone_hash = profile.phone_ciphertext = profile.phone_nonce = profile.phone_key_id = None
                profile.nickname = None
                profile.extra_data = {
                    key: value
                    for key, value in (profile.extra_data or {}).items()
                    if key not in {"region", "intention"}
                }
                suppressed = True
        db.add(
            ConsumerConsentAction(
                id=uuid7(),
                tenant_id=tenant_id,
                consent_id=record.id,
                policy_id=record.policy_id,
                consumer_id=record.consumer_id,
                action="withdraw",
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                visitor_subject_hash=subject_hash,
                result_status="withdrawn",
            )
        )
        await db.flush()
        return {
            "consent_id": record.id,
            "status": "withdrawn",
            "consumer_id": record.consumer_id,
            "contact_suppressed": suppressed,
            "withdrawn_at": record.withdrawn_at,
            "replayed": False,
        }
    row = await _call_authority(
        db,
        "SELECT * FROM public.withdraw_consumer_consent(:tenant_id,:consent_id,:audit_id,:scan_event_id,"
        ":scan_time,:public_id,:visitor_id,:token_consumer_id,:idempotency_key)",
        {
            "tenant_id": tenant_id,
            "consent_id": consent_id,
            "audit_id": uuid7(),
            "scan_event_id": scan_event_id,
            "scan_time": scan_time,
            "public_id": public_id,
            "visitor_id": visitor_id,
            "token_consumer_id": token_consumer_id,
            "idempotency_key": idempotency_key,
        },
    )
    if row is None:
        raise HTTPException(status_code=409, detail="consent_authority_invalid")
    return dict(row)


async def capture_consumer_lead_authority(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_consumer_id: uuid.UUID,
    consent_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
    phone_hash: str | None,
    phone_ciphertext: bytes | None,
    phone_nonce: bytes | None,
    phone_key_id: str | None,
    requested_name: str | None,
    requested_lead_extra: dict[str, str],
    idempotency_key: str,
) -> dict[str, Any]:
    if not _uses_postgresql(db):
        subject_hash, visitor = await _sqlite_subject(
            db, tenant_id, scan_event_id, scan_time, public_id, visitor_id, token_consumer_id
        )
        record = await db.scalar(
            select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.id == consent_id)
        )
        if record is None:
            raise HTTPException(status_code=404, detail="consent_authority_not_found")
        policy = await get_current_consumer_policy(db, tenant_id, "lead_capture")
        if (
            record.status != "granted"
            or record.purpose != "lead_capture"
            or record.visitor_subject_hash != subject_hash
            or record.public_id != public_id
            or record.policy_id != policy["policy_id"]
            or record.policy_digest != policy["policy_digest"]
        ):
            raise HTTPException(status_code=403, detail="consent_authority_denied")
        payload_hash = _payload_digest(
            {
                "consent_id": consent_id,
                "phone_hash": phone_hash,
                "name": requested_name,
                "extra": requested_lead_extra,
                "subject": subject_hash,
            }
        )
        existing = await db.scalar(
            select(ConsumerConsentAction).where(
                ConsumerConsentAction.tenant_id == tenant_id,
                ConsumerConsentAction.action == "lead_capture",
                ConsumerConsentAction.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.payload_hash != payload_hash:
                raise HTTPException(status_code=409, detail="consent_request_conflict")
            profile = await db.get(ConsumerProfile, existing.consumer_id)
            return {
                "outcome": "captured",
                "consumer_id": profile.id,
                "consent_id": consent_id,
                "contact_suppressed": profile.lead_contact_suppressed,
                "created": False,
                "replayed": True,
                "recorded_at": existing.occurred_at,
            }
        profile = await db.scalar(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.id == token_consumer_id
                if token_consumer_id is not None
                else ConsumerProfile.phone_hash == phone_hash,
            )
        )
        created = profile is None
        if profile is None:
            profile = ConsumerProfile(
                id=requested_consumer_id,
                tenant_id=tenant_id,
                phone_hash=phone_hash,
                phone_ciphertext=phone_ciphertext,
                phone_nonce=phone_nonce,
                phone_key_id=phone_key_id,
                nickname=requested_name,
                extra_data=requested_lead_extra,
            )
            db.add(profile)
            await db.flush()
        elif token_consumer_id is None or profile.id != token_consumer_id:
            raise HTTPException(status_code=403, detail="consent_authority_denied")
        conflicting_profile = await db.scalar(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_hash,
                ConsumerProfile.id != profile.id,
            )
        )
        if conflicting_profile is not None:
            raise HTTPException(status_code=409, detail="consent_request_conflict")
        profile.phone_hash = phone_hash
        profile.phone_ciphertext = phone_ciphertext
        profile.phone_nonce = phone_nonce
        profile.phone_key_id = phone_key_id
        profile.nickname = requested_name or profile.nickname
        profile.extra_data = {**(profile.extra_data or {}), **requested_lead_extra}
        profile.lead_contact_suppressed = False
        profile.lead_consent_id, profile.lead_scan_event_id = consent_id, scan_event_id
        profile.lead_scan_event_time, profile.lead_captured_at = scan_time, utcnow()
        visitor.consumer_id = profile.id
        record.consumer_id = profile.id
        db.add(
            ConsumerConsentAction(
                id=uuid7(),
                tenant_id=tenant_id,
                consent_id=consent_id,
                policy_id=record.policy_id,
                consumer_id=profile.id,
                action="lead_capture",
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                visitor_subject_hash=subject_hash,
                result_status="captured",
            )
        )
        await db.flush()
        return {
            "outcome": "captured",
            "consumer_id": profile.id,
            "consent_id": consent_id,
            "contact_suppressed": False,
            "created": created,
            "replayed": False,
            "recorded_at": profile.lead_captured_at,
        }
    row = await _call_authority(
        db,
        "SELECT * FROM public.capture_consumer_lead(:tenant_id,:requested_consumer_id,:audit_id,"
        ":consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,:token_consumer_id,:phone_hash,"
        ":phone_ciphertext,:phone_nonce,:phone_key_id,:requested_name,"
        "CAST(:requested_lead_extra AS jsonb),:idempotency_key)",
        {
            "tenant_id": tenant_id,
            "requested_consumer_id": requested_consumer_id,
            "audit_id": uuid7(),
            "consent_id": consent_id,
            "scan_event_id": scan_event_id,
            "scan_time": scan_time,
            "public_id": public_id,
            "visitor_id": visitor_id,
            "token_consumer_id": token_consumer_id,
            "phone_hash": phone_hash,
            "phone_ciphertext": phone_ciphertext,
            "phone_nonce": phone_nonce,
            "phone_key_id": phone_key_id,
            "requested_name": requested_name,
            "requested_lead_extra": __import__("json").dumps(requested_lead_extra, ensure_ascii=False),
            "idempotency_key": idempotency_key,
        },
    )
    if row is None:
        raise HTTPException(status_code=409, detail="lead_capture_invalid")
    return dict(row)


async def withdraw_consent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
) -> ConsentRecord | None:
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.id == consent_id,
            ConsentRecord.tenant_id == tenant_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None
    record.status = ConsentStatus.withdrawn
    record.withdrawn_at = utcnow()
    await db.flush()
    await db.refresh(record)
    return record


async def has_active_consent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consent_type: str,
    public_id: str | None = None,
    consumer_id: uuid.UUID | None = None,
) -> bool:
    """检查是否存在**当前有效**（granted 且未撤回）的指定类型同意。

    yimatong-zgb1.5 AC3：未同意前不采集手机号；撤回后停止使用。
    判定逻辑：查最近一条该（tenant, type, public_id/consumer_id）的 consent 记录，
    若状态为 granted → 有效；若最新一条为 withdrawn 或不存在 → 无效。

    优先级：consumer_id 命中 > public_id 命中（消费者级授权覆盖码级）。
    若都未提供，返回 False（保守：无法证明已授权）。
    """
    if not public_id and not consumer_id:
        return False

    filters = [
        ConsentRecord.tenant_id == tenant_id,
        ConsentRecord.consent_type == consent_type,
    ]
    if consumer_id is not None:
        filters.append(ConsentRecord.consumer_id == consumer_id)
    elif public_id is not None:
        filters.append(ConsentRecord.public_id == public_id)

    result = await db.execute(
        select(ConsentRecord.status).where(*filters).order_by(desc(ConsentRecord.granted_at)).limit(1)
    )
    latest_status = result.scalar_one_or_none()
    return latest_status == ConsentStatus.granted
