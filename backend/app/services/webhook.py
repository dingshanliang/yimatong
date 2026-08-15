"""Webhook / Open API 服务"""

import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint
from app.services.audit import write_audit_log
from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets
from app.utils.auth_rbac import VALID_API_KEY_ROLES, get_permissions_for_role
from app.utils.crypto import encrypt_bytes


def _generate_secret() -> str:
    """生成 webhook secret（whsec_ 前缀，32 字节随机）。"""
    return f"whsec_{secrets.token_hex(32)}"


def _webhook_url_digest(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedApiKey:
    id: uuid.UUID
    name: str
    key: str
    key_prefix: str
    role: str
    permissions: list[str]
    expires_at: datetime | None


@dataclass(frozen=True)
class ApiKeyLifecycleMaterial:
    idempotency_digest: str
    request_fingerprint: str
    secret: str
    key_prefix: str
    key_digest: str
    escrow_ciphertext: bytes


class ApiKeyLifecycleConflict(ValueError):
    pass


class ApiKeyActiveLimitReached(ValueError):
    pass


class WebhookEndpointConflict(ValueError):
    pass


@dataclass(frozen=True)
class WebhookEndpointView:
    id: uuid.UUID
    url: str
    events: list[str]
    description: str | None
    enabled: bool
    config_version: int
    batch_mode: bool
    batch_size: int


@dataclass(frozen=True)
class IssuedWebhookEndpoint(WebhookEndpointView):
    secret: str


@dataclass(frozen=True)
class WebhookDeliveryListView:
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: str
    event_type: str
    status: str
    retry_count: int
    last_response_code: int | None
    created_at: datetime | None


@dataclass(frozen=True)
class WebhookDeliveryDetailView(WebhookDeliveryListView):
    payload: dict
    next_retry_at: datetime | None
    last_response_body: str | None
    updated_at: datetime | None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _lifecycle_hmac(domain: str, value: str) -> str:
    try:
        pepper = bytes.fromhex(settings.hmac_pepper)
    except ValueError as exc:
        raise RuntimeError("HMAC pepper is not valid hexadecimal") from exc
    return hmac.new(
        pepper,
        f"api-key-lifecycle:{domain}:v1:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def build_api_key_lifecycle_material(
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    target_id: uuid.UUID | None,
    idempotency_key: str,
    payload: dict,
    candidate_id: uuid.UUID,
) -> ApiKeyLifecycleMaterial:
    if action not in {"issue", "rotate"}:
        raise ValueError("Unsupported API key lifecycle action")
    request_payload = {
        "action": action,
        "payload": payload,
        "target_id": str(target_id) if target_id else None,
    }
    request_json = _canonical_json(request_payload)
    secret_json = _canonical_json(
        {
            "actor_id": str(actor_id),
            "idempotency_key": idempotency_key,
            "request": request_payload,
            "tenant_id": str(tenant_id),
        }
    )
    secret = f"ymt_{_lifecycle_hmac('secret', secret_json)[:48]}"
    return ApiKeyLifecycleMaterial(
        idempotency_digest=_lifecycle_hmac("idempotency", idempotency_key),
        request_fingerprint=_lifecycle_hmac("request", request_json),
        secret=secret,
        key_prefix=secret[:12],
        key_digest=hashlib.sha256(secret.encode()).hexdigest(),
        escrow_ciphertext=encrypt_secrets({"v": 1, "api_key_id": str(candidate_id), "secret": secret}),
    )


def recover_api_key_secret(
    escrow_ciphertext: bytes,
    api_key_id: uuid.UUID,
    *,
    expected_prefix: str | None = None,
    expected_digest: str | None = None,
) -> str:
    try:
        escrow = decrypt_secrets(escrow_ciphertext)
    except Exception as exc:
        raise RuntimeError("API key escrow could not be decrypted") from exc
    secret = escrow.get("secret")
    if (
        set(escrow) != {"v", "api_key_id", "secret"}
        or escrow.get("v") != 1
        or escrow.get("api_key_id") != str(api_key_id)
        or not isinstance(secret, str)
        or re.fullmatch(r"ymt_[0-9a-f]{48}", secret) is None
    ):
        raise RuntimeError("API key escrow payload is invalid")
    if expected_prefix is not None and not hmac.compare_digest(secret[:12], expected_prefix):
        raise RuntimeError("API key escrow does not match lifecycle material")
    if expected_digest is not None and not hmac.compare_digest(
        hashlib.sha256(secret.encode()).hexdigest(), expected_digest
    ):
        raise RuntimeError("API key escrow does not match lifecycle material")
    return secret


def _api_key_request_payload(
    *,
    name: str,
    role: str,
    expires_at: datetime | None,
    permanent_reason: str | None = None,
) -> dict:
    return {
        "expires_at": _as_utc(expires_at).isoformat() if expires_at else None,
        "name": name,
        "permanent_reason": permanent_reason,
        "role": role,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _returned_escrow(value) -> bytes:
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("API key lifecycle returned invalid escrow") from exc


def _audit_details(*, key_prefix: str, role: str, expires_at: datetime | None, **extra) -> dict:
    return {
        "key_prefix": key_prefix,
        "role": role,
        "expires_at": expires_at.isoformat() if expires_at else None,
        **extra,
    }


async def create_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    url: str,
    events: list[str],
    *,
    actor_id: uuid.UUID | None = None,
    description: str | None = None,
    batch_mode: bool = False,
    batch_size: int = 100,
) -> IssuedWebhookEndpoint:
    endpoint_id = uuid7()
    secret = _generate_secret()
    aad = f"webhook-endpoint:{tenant_id}:{endpoint_id}".encode()
    ciphertext, nonce, key_id = encrypt_bytes(secret.encode(), aad=aad)
    if db.get_bind().dialect.name == "postgresql":
        result = await db.execute(
            text(
                "SELECT * FROM public.create_webhook_endpoint("
                ":tenant_id,:auth_session_id,:endpoint_id,:url,CAST(:events AS jsonb),:description,"
                ":batch_mode,:batch_size,:secret_ciphertext,:secret_nonce,:secret_key_id)"
            ),
            {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "endpoint_id": endpoint_id,
                "url": url,
                "events": _canonical_json(events),
                "description": description,
                "batch_mode": batch_mode,
                "batch_size": batch_size,
                "secret_ciphertext": ciphertext,
                "secret_nonce": nonce,
                "secret_key_id": key_id,
            },
        )
        returned = result.mappings().one()
        if uuid.UUID(str(returned["endpoint_id"])) != endpoint_id:
            raise RuntimeError("Webhook endpoint authority returned an unexpected identifier")
        config_version = int(returned["config_version"])
    else:
        ep = WebhookEndpoint(
            id=endpoint_id,
            tenant_id=tenant_id,
            url=url,
            events=events,
            secret_ciphertext=ciphertext,
            secret_nonce=nonce,
            secret_key_id=key_id,
            description=description,
            batch_mode=batch_mode,
            batch_size=batch_size,
        )
        db.add(ep)
        await db.flush()
        config_version = ep.config_version
    if actor_id is not None:
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="webhook_endpoint_created",
            resource=f"webhook_endpoint:{endpoint_id}",
            details={"config_version": config_version, "url_digest": _webhook_url_digest(url)},
        )
    return IssuedWebhookEndpoint(
        id=endpoint_id,
        url=url,
        events=events,
        description=description,
        enabled=True,
        config_version=config_version,
        batch_mode=batch_mode,
        batch_size=batch_size,
        secret=secret,
    )


async def update_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    expected_version: int,
    url: str | None = None,
    events: list[str] | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    batch_mode: bool | None = None,
    batch_size: int | None = None,
) -> WebhookEndpointView | None:
    result = await db.execute(
        select(
            WebhookEndpoint.id,
            WebhookEndpoint.url,
            WebhookEndpoint.events,
            WebhookEndpoint.description,
            WebhookEndpoint.enabled,
            WebhookEndpoint.config_version,
            WebhookEndpoint.batch_mode,
            WebhookEndpoint.batch_size,
        ).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
    )
    current = result.one_or_none()
    if current is None:
        return None
    if current.config_version != expected_version:
        raise WebhookEndpointConflict("Webhook endpoint version conflict")
    next_url = url if url is not None else current.url
    next_events = events if events is not None else list(current.events)
    next_description = description if description is not None else current.description
    next_enabled = enabled if enabled is not None else current.enabled
    next_batch_mode = batch_mode if batch_mode is not None else current.batch_mode
    next_batch_size = batch_size if batch_size is not None else current.batch_size
    if db.get_bind().dialect.name == "postgresql":
        next_version = await db.scalar(
            text(
                "SELECT public.update_webhook_endpoint("
                ":tenant_id,:auth_session_id,:endpoint_id,:expected_version,:url,CAST(:events AS jsonb),"
                ":description,:enabled,:batch_mode,:batch_size,NULL,NULL,NULL)"
            ),
            {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "endpoint_id": endpoint_id,
                "expected_version": expected_version,
                "url": next_url,
                "events": _canonical_json(next_events),
                "description": next_description,
                "enabled": next_enabled,
                "batch_mode": next_batch_mode,
                "batch_size": next_batch_size,
            },
        )
        view = WebhookEndpointView(
            id=endpoint_id,
            url=next_url,
            events=next_events,
            description=next_description,
            enabled=next_enabled,
            config_version=int(next_version),
            batch_mode=next_batch_mode,
            batch_size=next_batch_size,
        )
    else:
        ep = await db.get(WebhookEndpoint, endpoint_id)
        if ep is None or ep.tenant_id != tenant_id:
            return None
        ep.url = next_url
        ep.events = next_events
        ep.description = next_description
        ep.enabled = next_enabled
        ep.batch_mode = next_batch_mode
        ep.batch_size = next_batch_size
        ep.config_version += 1
        await db.flush()
        await db.refresh(ep)
        view = _endpoint_view(ep)
    if actor_id is not None:
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="webhook_endpoint_updated",
            resource=f"webhook_endpoint:{endpoint_id}",
            details={"config_version": view.config_version, "url_digest": _webhook_url_digest(view.url)},
        )
    return view


async def delete_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    expected_version: int,
) -> bool:
    result = await db.execute(
        select(WebhookEndpoint.id, WebhookEndpoint.config_version).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
    )
    current = result.one_or_none()
    if current is None:
        return False
    if current.config_version != expected_version:
        raise WebhookEndpointConflict("Webhook endpoint version conflict")
    if db.get_bind().dialect.name == "postgresql":
        deleted_id = await db.scalar(
            text("SELECT public.delete_webhook_endpoint(:tenant_id,:auth_session_id,:endpoint_id,:expected_version)"),
            {
                "tenant_id": tenant_id,
                "auth_session_id": auth_session_id,
                "endpoint_id": endpoint_id,
                "expected_version": expected_version,
            },
        )
        deleted = deleted_id is True
    else:
        ep = await db.get(WebhookEndpoint, endpoint_id)
        if ep is None or ep.tenant_id != tenant_id:
            return False
        await db.delete(ep)
        await db.flush()
        deleted = True
    if deleted and actor_id is not None:
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="webhook_endpoint_deleted",
            resource=f"webhook_endpoint:{endpoint_id}",
            details={"config_version": expected_version},
        )
    return deleted


async def list_webhook_endpoints(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[WebhookEndpointView]:
    result = await db.execute(
        select(
            WebhookEndpoint.id,
            WebhookEndpoint.url,
            WebhookEndpoint.events,
            WebhookEndpoint.description,
            WebhookEndpoint.enabled,
            WebhookEndpoint.config_version,
            WebhookEndpoint.batch_mode,
            WebhookEndpoint.batch_size,
        )
        .where(WebhookEndpoint.tenant_id == tenant_id)
        .order_by(WebhookEndpoint.id.desc())
    )
    return [WebhookEndpointView(*row) for row in result.all()]


def _endpoint_view(endpoint: WebhookEndpoint) -> WebhookEndpointView:
    return WebhookEndpointView(
        id=endpoint.id,
        url=endpoint.url,
        events=list(endpoint.events),
        description=endpoint.description,
        enabled=endpoint.enabled,
        config_version=endpoint.config_version,
        batch_mode=endpoint.batch_mode,
        batch_size=endpoint.batch_size,
    )


async def create_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    name: str,
    role: str = "data_reader",
    expires_at: datetime | None = None,
    *,
    idempotency_key: str,
    permanent_reason: str | None = None,
) -> IssuedApiKey:
    if role not in VALID_API_KEY_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_API_KEY_ROLES}")
    permissions = get_permissions_for_role(role)
    key_id = uuid7()
    audit_id = uuid7()
    material = build_api_key_lifecycle_material(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="issue",
        target_id=None,
        idempotency_key=idempotency_key,
        payload=_api_key_request_payload(
            name=name,
            role=role,
            expires_at=expires_at,
            permanent_reason=permanent_reason,
        ),
        candidate_id=key_id,
    )
    if db.get_bind().dialect.name == "postgresql":
        result = await db.execute(
            text(
                "SELECT * FROM public.issue_api_key("
                ":requested_id, :requested_tenant_id, :requested_actor_id, :requested_auth_session_id, "
                ":requested_name, :requested_key_prefix, :requested_key_digest, :requested_role, "
                ":requested_expires_at, :requested_idempotency_digest, :requested_request_fingerprint, "
                ":requested_escrow_ciphertext, :requested_permanent_reason, :requested_audit_id)"
            ),
            {
                "requested_id": key_id,
                "requested_tenant_id": tenant_id,
                "requested_actor_id": actor_id,
                "requested_auth_session_id": auth_session_id,
                "requested_name": name,
                "requested_key_prefix": material.key_prefix,
                "requested_key_digest": material.key_digest,
                "requested_role": role,
                "requested_expires_at": expires_at,
                "requested_idempotency_digest": material.idempotency_digest,
                "requested_request_fingerprint": material.request_fingerprint,
                "requested_escrow_ciphertext": material.escrow_ciphertext,
                "requested_permanent_reason": permanent_reason,
                "requested_audit_id": audit_id,
            },
        )
        returned = result.mappings().one()
        returned_id = uuid.UUID(str(returned["api_key_id"]))
        secret = recover_api_key_secret(
            _returned_escrow(returned["escrow_ciphertext"]),
            returned_id,
            expected_prefix=material.key_prefix,
            expected_digest=material.key_digest,
        )
    else:
        existing = await db.scalar(
            select(ApiKey).where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.created_by == actor_id,
                ApiKey.idempotency_key_digest == material.idempotency_digest,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != material.request_fingerprint:
                raise ApiKeyLifecycleConflict("API key idempotency payload conflicts")
            return IssuedApiKey(
                existing.id,
                existing.name,
                material.secret,
                existing.key_prefix,
                existing.role,
                list(existing.permissions),
                existing.expires_at,
            )
        active_count = await db.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.revoked.is_(False),
                (ApiKey.expires_at.is_(None) | (ApiKey.expires_at > datetime.now(UTC))),
            )
        )
        if (active_count or 0) >= 20:
            raise ApiKeyActiveLimitReached("API key active limit reached")
        api_key = ApiKey(
            id=key_id,
            tenant_id=tenant_id,
            name=name,
            key_prefix=material.key_prefix,
            key_digest=material.key_digest,
            role=role,
            permissions=permissions,
            expires_at=expires_at,
            created_by=actor_id,
            idempotency_key_digest=material.idempotency_digest,
            request_fingerprint=material.request_fingerprint,
            permanent_reason=permanent_reason,
        )
        db.add(api_key)
        await db.flush()
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="api_key_issued",
            resource=f"api_key:{key_id}",
            details=_audit_details(
                key_prefix=material.key_prefix,
                role=role,
                expires_at=expires_at,
                permanent_reason=permanent_reason,
            ),
        )
        returned_id = key_id
        secret = material.secret
    return IssuedApiKey(
        returned_id,
        name,
        secret,
        secret[:12],
        role,
        permissions,
        expires_at,
    )


async def rotate_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    *,
    idempotency_key: str,
) -> IssuedApiKey:
    old_key = await db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.tenant_id == tenant_id,
        )
    )
    if old_key is None:
        raise ValueError("API key not found")

    new_key_id = uuid7()
    audit_id = uuid7()
    permissions = get_permissions_for_role(old_key.role)
    material = build_api_key_lifecycle_material(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="rotate",
        target_id=key_id,
        idempotency_key=idempotency_key,
        payload=_api_key_request_payload(
            name=old_key.name,
            role=old_key.role,
            expires_at=old_key.expires_at,
        ),
        candidate_id=new_key_id,
    )
    if db.get_bind().dialect.name == "postgresql":
        result = await db.execute(
            text(
                "SELECT * FROM public.rotate_api_key("
                ":requested_old_id, :requested_new_id, :requested_tenant_id, :requested_actor_id, "
                ":requested_auth_session_id, :requested_name, :requested_key_prefix, :requested_key_digest, "
                ":requested_role, :requested_expires_at, :requested_idempotency_digest, "
                ":requested_request_fingerprint, :requested_escrow_ciphertext, :requested_audit_id)"
            ),
            {
                "requested_old_id": key_id,
                "requested_new_id": new_key_id,
                "requested_tenant_id": tenant_id,
                "requested_actor_id": actor_id,
                "requested_auth_session_id": auth_session_id,
                "requested_name": old_key.name,
                "requested_key_prefix": material.key_prefix,
                "requested_key_digest": material.key_digest,
                "requested_role": old_key.role,
                "requested_expires_at": old_key.expires_at,
                "requested_idempotency_digest": material.idempotency_digest,
                "requested_request_fingerprint": material.request_fingerprint,
                "requested_escrow_ciphertext": material.escrow_ciphertext,
                "requested_audit_id": audit_id,
            },
        )
        returned = result.mappings().one()
        returned_id = uuid.UUID(str(returned["api_key_id"]))
        secret = recover_api_key_secret(
            _returned_escrow(returned["escrow_ciphertext"]),
            returned_id,
            expected_prefix=material.key_prefix,
            expected_digest=material.key_digest,
        )
    else:
        existing = await db.scalar(
            select(ApiKey).where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.created_by == actor_id,
                ApiKey.idempotency_key_digest == material.idempotency_digest,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != material.request_fingerprint:
                raise ApiKeyLifecycleConflict("API key idempotency payload conflicts")
            return IssuedApiKey(
                existing.id,
                existing.name,
                material.secret,
                existing.key_prefix,
                existing.role,
                list(existing.permissions),
                existing.expires_at,
            )
        if old_key.revoked or (old_key.expires_at is not None and _as_utc(old_key.expires_at) <= datetime.now(UTC)):
            raise ApiKeyLifecycleConflict("API key is no longer active")
        old_key.revoked = True
        old_key.revoked_at = datetime.now(UTC)
        db.add(
            ApiKey(
                id=new_key_id,
                tenant_id=tenant_id,
                name=old_key.name,
                key_prefix=material.key_prefix,
                key_digest=material.key_digest,
                role=old_key.role,
                permissions=permissions,
                expires_at=old_key.expires_at,
                rotated_from_id=old_key.id,
                created_by=actor_id,
                idempotency_key_digest=material.idempotency_digest,
                request_fingerprint=material.request_fingerprint,
                permanent_reason=old_key.permanent_reason,
            )
        )
        await db.flush()
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="api_key_rotated",
            resource=f"api_key:{new_key_id}",
            details=_audit_details(
                key_prefix=material.key_prefix,
                role=old_key.role,
                expires_at=old_key.expires_at,
                permanent_reason=old_key.permanent_reason,
                rotated_from_id=str(old_key.id),
            ),
        )
        returned_id = new_key_id
        secret = material.secret
    return IssuedApiKey(
        returned_id,
        old_key.name,
        secret,
        secret[:12],
        old_key.role,
        permissions,
        old_key.expires_at,
    )


async def list_api_keys(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ApiKey], int]:
    filters = [ApiKey.tenant_id == tenant_id, ApiKey.revoked.is_(False)]
    total = await db.scalar(select(func.count()).select_from(ApiKey).where(*filters)) or 0
    result = await db.execute(
        select(ApiKey).where(*filters).order_by(ApiKey.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def revoke_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_id: uuid.UUID,
    auth_session_id: uuid.UUID,
) -> bool:
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id))
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("API key not found")
    audit_id = uuid7()
    if db.get_bind().dialect.name == "postgresql":
        revoked_id = await db.scalar(
            text(
                "SELECT public.revoke_api_key("
                ":requested_key_id, :requested_tenant_id, :requested_actor_id, "
                ":requested_auth_session_id, :requested_audit_id)"
            ),
            {
                "requested_key_id": key_id,
                "requested_tenant_id": tenant_id,
                "requested_actor_id": actor_id,
                "requested_auth_session_id": auth_session_id,
                "requested_audit_id": audit_id,
            },
        )
        if revoked_id is None:
            raise ValueError("API key not found")
        if revoked_id != key_id:
            raise RuntimeError("API key revocation did not return the requested identifier")
    else:
        key.revoked = True
        key.revoked_at = datetime.now(UTC)
        await db.flush()
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="api_key_revoked",
            resource=f"api_key:{key_id}",
            details=_audit_details(key_prefix=key.key_prefix, role=key.role, expires_at=key.expires_at),
        )
    return True


async def list_deliveries(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[WebhookDeliveryListView], int]:
    filters = [WebhookDelivery.tenant_id == tenant_id]
    if status:
        filters.append(WebhookDelivery.status == status)

    count_stmt = select(func.count()).select_from(WebhookDelivery).where(*filters)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(
            WebhookDelivery.id,
            WebhookDelivery.endpoint_id,
            WebhookDelivery.event_id,
            WebhookDelivery.event_type,
            WebhookDelivery.status,
            WebhookDelivery.retry_count,
            WebhookDelivery.last_response_code,
            WebhookDelivery.created_at,
        )
        .where(*filters)
        .order_by(WebhookDelivery.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return [WebhookDeliveryListView(*row) for row in result.all()], total


async def get_webhook_delivery(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
) -> WebhookDeliveryDetailView | None:
    """Load the operational delivery projection without endpoint secret material."""

    row = (
        await db.execute(
            select(
                WebhookDelivery.id,
                WebhookDelivery.endpoint_id,
                WebhookDelivery.event_id,
                WebhookDelivery.event_type,
                WebhookDelivery.status,
                WebhookDelivery.retry_count,
                WebhookDelivery.last_response_code,
                WebhookDelivery.created_at,
                WebhookDelivery.payload,
                WebhookDelivery.next_retry_at,
                WebhookDelivery.last_response_body,
                WebhookDelivery.updated_at,
            ).where(
                WebhookDelivery.id == delivery_id,
                WebhookDelivery.tenant_id == tenant_id,
            )
        )
    ).one_or_none()
    return WebhookDeliveryDetailView(*row) if row is not None else None
