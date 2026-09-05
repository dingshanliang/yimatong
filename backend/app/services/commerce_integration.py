"""Secure cross-product connection, credential, handoff, and message boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from fastapi import HTTPException
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import (
    _session_uses_postgresql,
    callback_session_factory,
    control_session_factory,
    set_session_tenant_context,
)
from app.models.commerce_integration import (
    CommerceConnection,
    CommerceConnectionEvent,
    CommerceIdentityHandoff,
    CommerceIntegrationMessage,
    CommerceMemberReference,
    CommerceServiceCredential,
)
from app.models.commerce_order import CommerceOrderFact, CommerceProductMapping, CommerceRepurchaseAttribution
from app.models.member import BrandMembership
from app.services.connectors.secrets import SecretsError, decrypt_secrets, encrypt_secrets
from app.utils import utcnow

CredentialDirection = Literal["yimatong_to_commerce", "commerce_to_yimatong"]


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _secret() -> tuple[str, str]:
    raw = f"ymc_{secrets.token_urlsafe(32)}"
    return raw, raw[:16]


def _member_ref(tenant_id: uuid.UUID, connection_id: uuid.UUID, membership_id: uuid.UUID) -> str:
    """Derive a versioned stable opaque reference independent of key rotation."""

    material = f"commerce-member-ref:v1:{tenant_id}:{connection_id}:{membership_id}".encode()
    return "cmr_" + hashlib.sha256(material).hexdigest()[:32]


def _credential_aad(
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    credential_id: uuid.UUID,
    direction: CredentialDirection,
    version: int,
) -> bytes:
    return f"commerce-credential:v1:{tenant_id}:{connection_id}:{credential_id}:{direction}:{version}".encode()


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _aware_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trips to the UTC contract."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _call_authority(
    db: AsyncSession,
    function_name: Literal[
        "mutate_commerce_connection_authority",
        "ensure_commerce_member_reference_authority",
        "mutate_commerce_handoff_authority",
        "accept_commerce_message_authority",
        "create_commerce_product_mapping_authority",
    ],
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
) -> Any:
    try:
        return await db.scalar(
            text(f"SELECT public.{function_name}(:tenant_id,CAST(:payload AS jsonb))"),
            {"tenant_id": tenant_id, "payload": json.dumps(payload, separators=(",", ":"), default=str)},
        )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        detail = str(exc.orig).splitlines()[0].split(": ", 1)[-1]
        status = 403 if sqlstate == "42501" else 409
        raise HTTPException(status_code=status, detail=detail) from exc


def _connection_dict(connection: CommerceConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "external_tenant_ref": connection.external_tenant_ref,
        "external_shop_ref": connection.external_shop_ref,
        "base_url": connection.base_url,
        "capabilities": connection.capabilities,
        "status": connection.status,
        "version": connection.version,
        "disconnected_at": connection.disconnected_at,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


async def get_commerce_connection(
    db: AsyncSession, tenant_id: uuid.UUID, connection_id: uuid.UUID, *, active_only: bool = False
) -> CommerceConnection:
    statement = select(CommerceConnection).where(
        CommerceConnection.tenant_id == tenant_id,
        CommerceConnection.id == connection_id,
    )
    if active_only:
        statement = statement.where(CommerceConnection.status == "active")
    if _session_uses_postgresql(db):
        statement = statement.execution_options(populate_existing=True)
    connection = await db.scalar(statement)
    if connection is None:
        raise HTTPException(status_code=404, detail="commerce_connection_not_found")
    return connection


async def create_commerce_product_mapping(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    external_product_ref: str,
    product_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
) -> CommerceProductMapping:
    await set_session_tenant_context(db, tenant_id)
    mapping_id = uuid7()
    returned = await _call_authority(
        db,
        "create_commerce_product_mapping_authority",
        tenant_id,
        {
            "mapping_id": mapping_id,
            "connection_id": connection_id,
            "external_product_ref": external_product_ref,
            "product_id": product_id,
            "actor_id": actor_id,
            "auth_session_id": auth_session_id,
        },
    )
    statement = select(CommerceProductMapping).where(
        CommerceProductMapping.tenant_id == tenant_id,
        CommerceProductMapping.id == uuid.UUID(str(returned)),
    )
    return (await db.scalars(statement.execution_options(populate_existing=True))).one()


async def list_commerce_connections(db: AsyncSession, tenant_id: uuid.UUID) -> list[CommerceConnection]:
    return list(
        (
            await db.scalars(
                select(CommerceConnection)
                .where(CommerceConnection.tenant_id == tenant_id)
                .order_by(CommerceConnection.created_at.desc())
            )
        ).all()
    )


async def create_commerce_connection(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    external_tenant_ref: str,
    external_shop_ref: str,
    base_url: str,
    capabilities: list[str],
    idempotency_key: str,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID | None = None,
) -> tuple[CommerceConnection, list[dict[str, Any]]]:
    terms = {
        "external_tenant_ref": external_tenant_ref.strip(),
        "external_shop_ref": external_shop_ref.strip(),
        "base_url": base_url.rstrip("/"),
        "capabilities": sorted(set(capabilities)),
    }
    payload_digest = _digest(terms)
    prior = await db.scalar(
        select(CommerceConnectionEvent).where(
            CommerceConnectionEvent.tenant_id == tenant_id,
            CommerceConnectionEvent.idempotency_key == idempotency_key,
        )
    )
    if prior is not None:
        if prior.event_type != "connected" or prior.payload_digest != payload_digest:
            raise _conflict("commerce_idempotency_conflict")
        connection = await get_commerce_connection(db, tenant_id, prior.connection_id)
        return connection, []

    connection_id = uuid7()
    now = utcnow()
    credentials: list[dict[str, Any]] = []
    for direction in ("yimatong_to_commerce", "commerce_to_yimatong"):
        raw_secret, key_prefix = _secret()
        credential_id = uuid7()
        credentials.append(
            {
                "id": credential_id,
                "direction": direction,
                "version": 1,
                "key_prefix": key_prefix,
                "secret": raw_secret,
                "secret_ciphertext": encrypt_secrets(
                    {"secret": raw_secret},
                    associated_data=_credential_aad(tenant_id, connection_id, credential_id, direction, 1),
                ).hex(),
                "valid_from": now,
                "valid_until": now + timedelta(days=90),
            }
        )

    if _session_uses_postgresql(db):
        returned = await _call_authority(
            db,
            "mutate_commerce_connection_authority",
            tenant_id,
            {
                "action": "connect",
                "connection_id": connection_id,
                **terms,
                "credentials": [{key: value for key, value in item.items() if key != "secret"} for item in credentials],
                "actor_id": actor_id,
                "auth_session_id": auth_session_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        connection_id = uuid.UUID(str(returned))
        connection = await get_commerce_connection(db, tenant_id, connection_id)
    else:
        connection = CommerceConnection(
            id=connection_id,
            tenant_id=tenant_id,
            external_tenant_ref=terms["external_tenant_ref"],
            external_shop_ref=terms["external_shop_ref"],
            base_url=terms["base_url"],
            capabilities=terms["capabilities"],
            status="active",
            version=1,
            created_by=actor_id,
        )
        db.add(connection)
        for item in credentials:
            db.add(
                CommerceServiceCredential(
                    id=item["id"],
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    direction=item["direction"],
                    version=1,
                    key_prefix=item["key_prefix"],
                    secret_ciphertext=bytes.fromhex(item["secret_ciphertext"]),
                    valid_from=item["valid_from"],
                    valid_until=item["valid_until"],
                )
            )
        db.add(
            CommerceConnectionEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=connection_id,
                event_type="connected",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                actor_id=actor_id,
                details=terms,
            )
        )
        await db.flush()
    public_credentials = [
        {key: value for key, value in item.items() if key != "secret_ciphertext"} for item in credentials
    ]
    return connection, public_credentials


async def rotate_commerce_credential(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    direction: CredentialDirection,
    idempotency_key: str,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    await get_commerce_connection(db, tenant_id, connection_id, active_only=True)
    payload_digest = _digest({"connection_id": connection_id, "direction": direction})
    prior = await db.scalar(
        select(CommerceConnectionEvent).where(
            CommerceConnectionEvent.tenant_id == tenant_id,
            CommerceConnectionEvent.idempotency_key == idempotency_key,
        )
    )
    if prior is not None:
        if prior.event_type != "credential_rotated" or prior.payload_digest != payload_digest:
            raise _conflict("commerce_idempotency_conflict")
        raise _conflict("credential_secret_already_returned")

    current_version = int(
        await db.scalar(
            select(func.coalesce(func.max(CommerceServiceCredential.version), 0)).where(
                CommerceServiceCredential.tenant_id == tenant_id,
                CommerceServiceCredential.connection_id == connection_id,
                CommerceServiceCredential.direction == direction,
            )
        )
        or 0
    )
    now = utcnow()
    raw_secret, key_prefix = _secret()
    credential_id = uuid7()
    credential = {
        "id": credential_id,
        "direction": direction,
        "version": current_version + 1,
        "key_prefix": key_prefix,
        "secret": raw_secret,
        "secret_ciphertext": encrypt_secrets(
            {"secret": raw_secret},
            associated_data=_credential_aad(tenant_id, connection_id, credential_id, direction, current_version + 1),
        ).hex(),
        "valid_from": now,
        "valid_until": now + timedelta(days=90),
    }
    if _session_uses_postgresql(db):
        await _call_authority(
            db,
            "mutate_commerce_connection_authority",
            tenant_id,
            {
                "action": "rotate_credential",
                "connection_id": connection_id,
                "credential": {key: value for key, value in credential.items() if key != "secret"},
                "actor_id": actor_id,
                "auth_session_id": auth_session_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
    else:
        old = await db.scalar(
            select(CommerceServiceCredential).where(
                CommerceServiceCredential.tenant_id == tenant_id,
                CommerceServiceCredential.connection_id == connection_id,
                CommerceServiceCredential.direction == direction,
                CommerceServiceCredential.version == current_version,
            )
        )
        if old is not None:
            old.overlap_until = min(_aware_utc(old.valid_until), now + timedelta(hours=24))
        db.add(
            CommerceServiceCredential(
                id=credential["id"],
                tenant_id=tenant_id,
                connection_id=connection_id,
                direction=direction,
                version=credential["version"],
                key_prefix=key_prefix,
                secret_ciphertext=bytes.fromhex(credential["secret_ciphertext"]),
                valid_from=now,
                valid_until=credential["valid_until"],
            )
        )
        db.add(
            CommerceConnectionEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=connection_id,
                event_type="credential_rotated",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                actor_id=actor_id,
                details={"direction": direction, "version": credential["version"]},
            )
        )
        await db.flush()
    return {key: value for key, value in credential.items() if key != "secret_ciphertext"}


async def revoke_commerce_credential(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    credential_id: uuid.UUID,
    idempotency_key: str,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID | None = None,
) -> None:
    payload_digest = _digest({"connection_id": connection_id, "credential_id": credential_id})
    if _session_uses_postgresql(db):
        await _call_authority(
            db,
            "mutate_commerce_connection_authority",
            tenant_id,
            {
                "action": "revoke_credential",
                "connection_id": connection_id,
                "credential_id": credential_id,
                "actor_id": actor_id,
                "auth_session_id": auth_session_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return
    credential = await db.scalar(
        select(CommerceServiceCredential).where(
            CommerceServiceCredential.tenant_id == tenant_id,
            CommerceServiceCredential.connection_id == connection_id,
            CommerceServiceCredential.id == credential_id,
        )
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="commerce_credential_not_found")
    credential.revoked_at = utcnow()
    db.add(
        CommerceConnectionEvent(
            id=uuid7(),
            tenant_id=tenant_id,
            connection_id=connection_id,
            event_type="credential_revoked",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_id=actor_id,
            details={"credential_id": str(credential_id), "direction": credential.direction},
        )
    )
    await db.flush()


async def disconnect_commerce_connection(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    idempotency_key: str,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID | None = None,
) -> CommerceConnection:
    payload_digest = _digest({"connection_id": connection_id})
    if _session_uses_postgresql(db):
        returned = await _call_authority(
            db,
            "mutate_commerce_connection_authority",
            tenant_id,
            {
                "action": "disconnect",
                "connection_id": connection_id,
                "actor_id": actor_id,
                "auth_session_id": auth_session_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_commerce_connection(db, tenant_id, uuid.UUID(str(returned)))
    connection = await get_commerce_connection(db, tenant_id, connection_id)
    if connection.status == "active":
        now = utcnow()
        connection.status = "disconnected"
        connection.disconnected_at = now
        connection.version += 1
        connection.updated_at = now
        credentials = await db.scalars(
            select(CommerceServiceCredential).where(
                CommerceServiceCredential.tenant_id == tenant_id,
                CommerceServiceCredential.connection_id == connection_id,
                CommerceServiceCredential.revoked_at.is_(None),
            )
        )
        for credential in credentials:
            credential.revoked_at = now
        db.add(
            CommerceConnectionEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=connection_id,
                event_type="disconnected",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                actor_id=actor_id,
                details={},
            )
        )
        await db.flush()
    return connection


async def _ensure_member_reference(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    membership_id: uuid.UUID,
    consumer_id: uuid.UUID | None = None,
    scan_event_id: uuid.UUID | None = None,
    public_id: str | None = None,
) -> CommerceMemberReference:
    existing = await db.scalar(
        select(CommerceMemberReference).where(
            CommerceMemberReference.tenant_id == tenant_id,
            CommerceMemberReference.connection_id == connection_id,
            CommerceMemberReference.membership_id == membership_id,
        )
    )
    if existing is not None:
        return existing
    reference_id = uuid7()
    member_ref = f"cmr_{secrets.token_urlsafe(24)}"
    if _session_uses_postgresql(db):
        async with callback_session_factory() as callback_db, callback_db.begin():
            await set_session_tenant_context(callback_db, tenant_id)
            returned = await _call_authority(
                callback_db,
                "ensure_commerce_member_reference_authority",
                tenant_id,
                {
                    "connection_id": connection_id,
                    "membership_id": membership_id,
                    "consumer_id": consumer_id,
                    "scan_event_id": scan_event_id,
                    "public_id": public_id,
                    "reference_id": reference_id,
                    "member_ref": member_ref,
                },
            )
        reference_id = uuid.UUID(str(returned))
        statement = select(CommerceMemberReference).where(
            CommerceMemberReference.tenant_id == tenant_id,
            CommerceMemberReference.id == reference_id,
        )
        return (await db.scalars(statement.execution_options(populate_existing=True))).one()
    reference = CommerceMemberReference(
        id=reference_id,
        tenant_id=tenant_id,
        connection_id=connection_id,
        membership_id=membership_id,
        member_ref=member_ref,
    )
    db.add(reference)
    await db.flush()
    return reference


async def issue_commerce_handoff(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    membership_id: uuid.UUID,
    idempotency_key: str,
    consumer_id: uuid.UUID | None = None,
    scan_event_id: uuid.UUID | None = None,
    public_id: str | None = None,
) -> tuple[str, CommerceConnection]:
    connection = await get_commerce_connection(db, tenant_id, connection_id, active_only=True)
    membership = await db.scalar(
        select(BrandMembership).where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.id == membership_id,
            BrandMembership.status == "active",
        )
    )
    if membership is None:
        raise _conflict("active_membership_required")
    existing_reference = await db.scalar(
        select(CommerceMemberReference).where(
            CommerceMemberReference.tenant_id == tenant_id,
            CommerceMemberReference.connection_id == connection_id,
            CommerceMemberReference.membership_id == membership_id,
        )
    )
    reference_id = existing_reference.id if existing_reference else uuid7()
    member_ref = (
        existing_reference.member_ref if existing_reference else _member_ref(tenant_id, connection_id, membership_id)
    )
    reference = None
    if not _session_uses_postgresql(db):
        reference = await _ensure_member_reference(
            db,
            tenant_id=tenant_id,
            connection_id=connection_id,
            membership_id=membership_id,
            consumer_id=consumer_id,
            scan_event_id=scan_event_id,
            public_id=public_id,
        )
        reference_id = reference.id
        member_ref = reference.member_ref
    now = utcnow()
    handoff_id = uuid7()
    token = jwt.encode(
        {
            "type": "commerce_identity_handoff",
            "tenant_id": str(tenant_id),
            "connection_id": str(connection_id),
            "handoff_id": str(handoff_id),
            "member_ref": member_ref,
            "target_shop": connection.external_shop_ref,
            "jti": str(uuid7()),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    payload_digest = _digest({"connection_id": connection_id, "membership_id": membership_id})
    if _session_uses_postgresql(db):
        async with callback_session_factory() as callback_db, callback_db.begin():
            await set_session_tenant_context(callback_db, tenant_id)
            await _call_authority(
                callback_db,
                "mutate_commerce_handoff_authority",
                tenant_id,
                {
                    "action": "issue",
                    "handoff_id": handoff_id,
                    "connection_id": connection_id,
                    "member_reference_id": reference_id,
                    "member_ref": member_ref,
                    "membership_id": membership_id,
                    "consumer_id": consumer_id,
                    "scan_event_id": scan_event_id,
                    "public_id": public_id,
                    "token_digest": token_digest,
                    "expires_at": now + timedelta(minutes=5),
                    "outbox_id": uuid7(),
                    "event_id": uuid7(),
                    "idempotency_key": idempotency_key,
                    "payload_digest": payload_digest,
                },
            )
    else:
        db.add(
            CommerceIdentityHandoff(
                id=handoff_id,
                tenant_id=tenant_id,
                connection_id=connection_id,
                member_reference_id=reference_id,
                token_digest=token_digest,
                expires_at=now + timedelta(minutes=5),
            )
        )
        db.add(
            CommerceIntegrationMessage(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=connection_id,
                direction="outbox",
                message_id=f"identity-handoff:{handoff_id}",
                message_version=1,
                message_type="identity.handoff.created",
                occurred_at=now,
                payload={"handoff_id": str(handoff_id), "member_ref": member_ref},
                payload_digest=_digest({"handoff_id": handoff_id, "member_ref": member_ref}),
                status="pending",
                attempt_count=0,
            )
        )
        db.add(
            CommerceConnectionEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=connection_id,
                event_type="handoff_issued",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                details={"handoff_id": str(handoff_id), "member_ref": member_ref},
            )
        )
        await db.flush()
    return token, connection


def decode_commerce_handoff(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "commerce_identity_handoff":
            raise ValueError
        for key in ("tenant_id", "connection_id", "handoff_id", "member_ref", "target_shop", "jti"):
            if not payload.get(key):
                raise ValueError
        uuid.UUID(payload["tenant_id"])
        uuid.UUID(payload["connection_id"])
        uuid.UUID(payload["handoff_id"])
        uuid.UUID(payload["jti"])
        return payload
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid_or_expired_commerce_handoff") from exc


async def redeem_commerce_handoff(
    db: AsyncSession, *, token: str, payload: dict[str, Any]
) -> tuple[CommerceConnection, str]:
    tenant_id = uuid.UUID(payload["tenant_id"])
    await set_session_tenant_context(db, tenant_id)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    if _session_uses_postgresql(db):
        async with callback_session_factory() as callback_db, callback_db.begin():
            await set_session_tenant_context(callback_db, tenant_id)
            member_ref = await _call_authority(
                callback_db,
                "mutate_commerce_handoff_authority",
                tenant_id,
                {
                    "action": "redeem",
                    "handoff_id": payload["handoff_id"],
                    "connection_id": payload["connection_id"],
                    "member_ref": payload["member_ref"],
                    "target_shop": payload["target_shop"],
                    "token_digest": token_digest,
                    "event_id": uuid7(),
                    "idempotency_key": f"handoff-redeem:{payload['jti']}",
                    "payload_digest": _digest({"handoff_id": payload["handoff_id"], "jti": payload["jti"]}),
                },
            )
    else:
        handoff = await db.scalar(
            select(CommerceIdentityHandoff).where(
                CommerceIdentityHandoff.tenant_id == tenant_id,
                CommerceIdentityHandoff.id == uuid.UUID(payload["handoff_id"]),
                CommerceIdentityHandoff.connection_id == uuid.UUID(payload["connection_id"]),
            )
        )
        if handoff is None or handoff.token_digest != token_digest or _aware_utc(handoff.expires_at) <= utcnow():
            raise HTTPException(status_code=401, detail="invalid_or_expired_commerce_handoff")
        if handoff.redeemed_at is not None:
            raise _conflict("commerce_handoff_already_redeemed")
        handoff.redeemed_at = utcnow()
        reference = await db.scalar(
            select(CommerceMemberReference).where(
                CommerceMemberReference.tenant_id == tenant_id,
                CommerceMemberReference.id == handoff.member_reference_id,
            )
        )
        member_ref = reference.member_ref if reference else None
        db.add(
            CommerceConnectionEvent(
                id=uuid7(),
                tenant_id=tenant_id,
                connection_id=handoff.connection_id,
                event_type="handoff_redeemed",
                idempotency_key=f"handoff-redeem:{payload['jti']}",
                payload_digest=_digest({"handoff_id": payload["handoff_id"], "jti": payload["jti"]}),
                details={"handoff_id": payload["handoff_id"], "member_ref": member_ref},
            )
        )
        await db.flush()
    connection = await get_commerce_connection(db, tenant_id, uuid.UUID(payload["connection_id"]), active_only=True)
    if connection.external_shop_ref != payload["target_shop"] or str(member_ref) != payload["member_ref"]:
        raise HTTPException(status_code=401, detail="commerce_handoff_scope_mismatch")
    return connection, str(member_ref)


async def resolve_commerce_credential(credential_id: uuid.UUID) -> tuple[CommerceServiceCredential, CommerceConnection]:
    async with control_session_factory() as control:
        await control.execute(text("SELECT set_config('app.tenant_id','',true)"))
        await control.execute(text("SELECT set_config('app.bypass_rls','true',true)"))
        credential = await control.scalar(
            select(CommerceServiceCredential).where(CommerceServiceCredential.id == credential_id)
        )
        if credential is None:
            raise HTTPException(status_code=401, detail="invalid_commerce_credential")
        connection = await control.scalar(
            select(CommerceConnection).where(
                CommerceConnection.tenant_id == credential.tenant_id,
                CommerceConnection.id == credential.connection_id,
            )
        )
        if connection is None:
            raise HTTPException(status_code=401, detail="invalid_commerce_credential")
        latest_version = int(
            await control.scalar(
                select(func.max(CommerceServiceCredential.version)).where(
                    CommerceServiceCredential.tenant_id == credential.tenant_id,
                    CommerceServiceCredential.connection_id == credential.connection_id,
                    CommerceServiceCredential.direction == credential.direction,
                )
            )
            or 0
        )
        now = utcnow()
        active = (
            connection.status == "active"
            and credential.direction == "commerce_to_yimatong"
            and credential.revoked_at is None
            and credential.valid_from <= now < credential.valid_until
            and (
                credential.version == latest_version
                or (credential.overlap_until is not None and now < credential.overlap_until)
            )
        )
        if not active:
            raise HTTPException(status_code=401, detail="expired_or_revoked_commerce_credential")
        control.expunge(credential)
        control.expunge(connection)
        return credential, connection


def verify_commerce_signature(
    *, credential: CommerceServiceCredential, timestamp: str, path: str, body: bytes, signature: str
) -> None:
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid_commerce_timestamp") from exc
    if abs(int(time.time()) - timestamp_value) > 300:
        raise HTTPException(status_code=401, detail="commerce_timestamp_outside_five_minute_window")
    try:
        secret = decrypt_secrets(
            credential.secret_ciphertext,
            associated_data=_credential_aad(
                credential.tenant_id,
                credential.connection_id,
                credential.id,
                credential.direction,
                credential.version,
            ),
        ).get("secret")
    except SecretsError as exc:
        raise HTTPException(status_code=503, detail="commerce_credential_unavailable") from exc
    if not isinstance(secret, str):
        raise HTTPException(status_code=503, detail="commerce_credential_unavailable")
    canonical = f"{timestamp}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
    expected = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid_commerce_signature")


async def accept_commerce_event(
    db: AsyncSession,
    *,
    credential: CommerceServiceCredential,
    connection: CommerceConnection,
    event: dict[str, Any],
    body_digest: str,
) -> tuple[CommerceIntegrationMessage, bool]:
    if connection.id != credential.connection_id or connection.tenant_id != credential.tenant_id:
        raise HTTPException(status_code=401, detail="invalid_commerce_credential")
    await set_session_tenant_context(db, credential.tenant_id)
    prior = await db.scalar(
        select(CommerceIntegrationMessage).where(
            CommerceIntegrationMessage.tenant_id == credential.tenant_id,
            CommerceIntegrationMessage.connection_id == credential.connection_id,
            CommerceIntegrationMessage.direction == "inbox",
            CommerceIntegrationMessage.message_id == event["event_id"],
            CommerceIntegrationMessage.message_version == event["event_version"],
        )
    )
    if prior is not None:
        if prior.payload_digest != body_digest or prior.message_type != event["event_type"]:
            raise _conflict("commerce_event_idempotency_conflict")
        if prior.projected_at is None and _session_uses_postgresql(db):
            async with callback_session_factory() as callback_db, callback_db.begin():
                await set_session_tenant_context(callback_db, credential.tenant_id)
                await callback_db.scalar(
                    text("SELECT public.project_commerce_order_event_authority(:tenant_id,:message_row_id)"),
                    {"tenant_id": credential.tenant_id, "message_row_id": prior.id},
                )
                await callback_db.scalar(
                    text("SELECT public.record_commerce_member_notification_authority(:tenant_id,:message_row_id)"),
                    {"tenant_id": credential.tenant_id, "message_row_id": prior.id},
                )
        return prior, True
    if event.get("member_ref"):
        member_reference = await db.scalar(
            select(CommerceMemberReference).where(
                CommerceMemberReference.tenant_id == credential.tenant_id,
                CommerceMemberReference.connection_id == credential.connection_id,
                CommerceMemberReference.member_ref == event["member_ref"],
            )
        )
        if member_reference is None:
            raise HTTPException(status_code=403, detail="commerce_member_reference_scope_mismatch")
    message_id = uuid7()
    event_payload = json.loads(json.dumps(event, separators=(",", ":"), ensure_ascii=False, default=str))
    payload = {
        "message_row_id": message_id,
        "connection_id": credential.connection_id,
        "credential_id": credential.id,
        "message_id": event["event_id"],
        "message_version": event["event_version"],
        "message_type": event["event_type"],
        "occurred_at": event["occurred_at"],
        "payload": event_payload,
        "payload_digest": body_digest,
    }
    if _session_uses_postgresql(db):
        async with callback_session_factory() as callback_db, callback_db.begin():
            await set_session_tenant_context(callback_db, credential.tenant_id)
            returned = await _call_authority(
                callback_db,
                "accept_commerce_message_authority",
                credential.tenant_id,
                payload,
            )
            await callback_db.scalar(
                text("SELECT public.project_commerce_order_event_authority(:tenant_id,:message_row_id)"),
                {"tenant_id": credential.tenant_id, "message_row_id": returned},
            )
            await callback_db.scalar(
                text("SELECT public.record_commerce_member_notification_authority(:tenant_id,:message_row_id)"),
                {"tenant_id": credential.tenant_id, "message_row_id": returned},
            )
        message_id = uuid.UUID(str(returned))
        statement = select(CommerceIntegrationMessage).where(
            CommerceIntegrationMessage.tenant_id == credential.tenant_id,
            CommerceIntegrationMessage.id == message_id,
        )
        message = (await db.scalars(statement.execution_options(populate_existing=True))).one()
    else:
        message = CommerceIntegrationMessage(
            id=message_id,
            tenant_id=credential.tenant_id,
            connection_id=credential.connection_id,
            direction="inbox",
            message_id=event["event_id"],
            message_version=event["event_version"],
            message_type=event["event_type"],
            credential_id=credential.id,
            occurred_at=event["occurred_at"],
            payload=event_payload,
            payload_digest=body_digest,
            status="accepted",
            attempt_count=0,
            accepted_at=utcnow(),
        )
        db.add(message)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise _conflict("commerce_event_idempotency_conflict") from exc
    return message, False


async def list_commerce_order_facts(
    db: AsyncSession, tenant_id: uuid.UUID, *, page: int = 1, page_size: int = 50
) -> tuple[list[dict[str, Any]], int]:
    """Return the tenant's auditable order and repurchase projection."""

    await set_session_tenant_context(db, tenant_id)
    base = (
        select(CommerceOrderFact, CommerceRepurchaseAttribution)
        .join(
            CommerceRepurchaseAttribution,
            (CommerceRepurchaseAttribution.tenant_id == CommerceOrderFact.tenant_id)
            & (CommerceRepurchaseAttribution.order_fact_id == CommerceOrderFact.id),
        )
        .where(CommerceOrderFact.tenant_id == tenant_id)
    )
    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = (
        await db.execute(
            base.order_by(CommerceOrderFact.paid_at.desc(), CommerceOrderFact.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return [
        {
            "id": order.id,
            "external_order_ref": order.external_order_ref,
            "status": order.status,
            "currency": order.currency,
            "is_member_order": attribution.is_member_order,
            "is_packaging_repurchase": attribution.is_packaging_repurchase,
            "entry_attributed": attribution.entry_attributed,
            "coupon_attributed": attribution.coupon_attributed,
            "product_original_amount_fen": attribution.product_original_amount_fen,
            "product_refunded_amount_fen": attribution.product_refunded_amount_fen,
            "net_product_sales_fen": attribution.net_product_sales_fen,
            "coverage_status": attribution.coverage_status,
            "trust_level": attribution.trust_level,
            "occurrence_at": attribution.occurrence_at,
            "member_cohort_at": attribution.member_cohort_at,
        }
        for order, attribution in rows
    ], total


async def commerce_repurchase_metrics(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Aggregate formal metrics without treating missing coverage as zero."""

    await set_session_tenant_context(db, tenant_id)
    attribution = CommerceRepurchaseAttribution
    row = (
        await db.execute(
            select(
                func.count().filter(attribution.is_member_order).label("member_orders"),
                func.count().filter(attribution.is_packaging_repurchase).label("packaging_repurchase_orders"),
                func.count(func.distinct(attribution.membership_id))
                .filter(attribution.is_packaging_repurchase)
                .label("repurchase_members"),
                func.coalesce(func.sum(attribution.net_product_sales_fen).filter(attribution.is_member_order), 0).label(
                    "net_product_sales_fen"
                ),
                func.count()
                .filter(attribution.entry_attributed & attribution.is_member_order)
                .label("entry_attributed_orders"),
                func.count()
                .filter(attribution.coupon_attributed & attribution.is_member_order)
                .label("coupon_attributed_orders"),
                func.count().filter(attribution.coverage_status == "complete").label("complete_coverage_orders"),
                func.count().filter(attribution.coverage_status != "complete").label("partial_or_missing_orders"),
            ).where(attribution.tenant_id == tenant_id)
        )
    ).one()
    return {key: int(getattr(row, key) or 0) for key in row._fields}


async def commerce_reconciliation(db: AsyncSession, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> dict[str, Any]:
    connection = await get_commerce_connection(db, tenant_id, connection_id)
    row = (
        await db.execute(
            select(
                func.count().filter(CommerceIntegrationMessage.direction == "inbox").label("inbox_accepted"),
                func.count()
                .filter(
                    CommerceIntegrationMessage.direction == "outbox",
                    CommerceIntegrationMessage.status == "pending",
                )
                .label("outbox_pending"),
                func.count()
                .filter(
                    CommerceIntegrationMessage.direction == "outbox",
                    CommerceIntegrationMessage.status == "delivered",
                )
                .label("outbox_delivered"),
                func.count()
                .filter(
                    CommerceIntegrationMessage.direction == "outbox",
                    CommerceIntegrationMessage.status == "failed",
                )
                .label("outbox_failed"),
                func.max(
                    case((CommerceIntegrationMessage.direction == "inbox", CommerceIntegrationMessage.created_at))
                ).label("last_inbox_at"),
                func.max(
                    case((CommerceIntegrationMessage.direction == "outbox", CommerceIntegrationMessage.created_at))
                ).label("last_outbox_at"),
            ).where(
                CommerceIntegrationMessage.tenant_id == tenant_id,
                CommerceIntegrationMessage.connection_id == connection_id,
            )
        )
    ).one()
    return {"connection_id": connection.id, "status": connection.status, **row._mapping}


def sign_commerce_request(secret: str, timestamp: int, path: str, body: bytes) -> str:
    """Test/client helper for the version-1 request signature contract."""

    canonical = f"{timestamp}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
