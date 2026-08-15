"""Authentication, authorization, and abuse controls for pilot learning APIs."""

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.auth_rbac import require_durable_session, require_permission

PILOT_MUTATION_RATE_LIMIT = 20
PILOT_MUTATION_RATE_WINDOW_SECONDS = 60
_pilot_security_cache = AsyncRedisCache(prefix="pilot_security", default_ttl=PILOT_MUTATION_RATE_WINDOW_SECONDS)


def canonical_pilot_payload_digest(payload: dict[str, Any]) -> str:
    """Hash one stable mutation representation for DB-owned idempotency."""

    def normalize(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    canonical = json.dumps(normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def pilot_authority_http_error(exc: DBAPIError) -> HTTPException:
    """Translate expected database-authority denials without leaking SQL details."""

    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    response = {
        "22023": (422, "Invalid pilot mutation", None),
        "23503": (404, "Pilot resource not found", None),
        "23505": (409, "Idempotency-Key payload conflicts", None),
        "23514": (409, "Carryover actions require an explicit disposition", None),
        "40001": (409, "Pilot record version conflict", None),
        "42501": (403, "Pilot authority denied", None),
        "55P03": (409, "Pilot record is busy; retry the request", {"Retry-After": "1"}),
    }.get(sqlstate)
    if response is None:
        raise exc
    status_code, detail, headers = response
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _session_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="A live login session is required") from exc


async def require_pilot_read_access(request: Request) -> uuid.UUID:
    """Admit direct brand or explicitly authorized acting-agency readers."""

    await require_durable_session(request)
    if getattr(request.state, "auth_method", None) != "jwt" or getattr(request.state, "role", None) not in {
        "admin",
        "operator",
    }:
        raise HTTPException(status_code=403, detail="Pilot analytics access required")
    await require_permission("analytics:view")(request)

    tenant_type = getattr(request.state, "tenant_type", None)
    acting_tenant_id = getattr(request.state, "acting_tenant_id", None)
    if tenant_type == "brand" and not acting_tenant_id:
        return _session_id(request)
    if (
        tenant_type == "agency"
        and acting_tenant_id
        and {"analytics", "campaigns"}.intersection(getattr(request.state, "agency_scopes", []) or [])
    ):
        return _session_id(request)
    raise HTTPException(status_code=403, detail="Pilot analytics access required")


async def require_retrospective_manage_access(request: Request) -> uuid.UUID:
    """Admit direct brand editors or campaigns-scoped acting-agency editors."""

    await require_durable_session(request)
    if getattr(request.state, "auth_method", None) != "jwt" or getattr(request.state, "role", None) not in {
        "admin",
        "operator",
    }:
        raise HTTPException(status_code=403, detail="Retrospective management access required")
    await require_permission("campaign:manage")(request)

    tenant_type = getattr(request.state, "tenant_type", None)
    acting_tenant_id = getattr(request.state, "acting_tenant_id", None)
    if tenant_type == "brand" and not acting_tenant_id:
        return _session_id(request)
    if (
        tenant_type == "agency"
        and acting_tenant_id
        and "campaigns" in (getattr(request.state, "agency_scopes", []) or [])
    ):
        return _session_id(request)
    raise HTTPException(status_code=403, detail="Retrospective management access required")


async def require_platform_pilot_correction_access(request: Request) -> uuid.UUID:
    """Return the durable cookie-only platform control-plane session."""

    if (
        getattr(request.state, "auth_method", None) != "platform_cookie"
        or getattr(request.state, "role", None) != "platform_admin"
        or getattr(request.state, "account_id", None) != "platform-admin"
        or getattr(request.state, "tenant_id", None) != "platform"
        or getattr(request.state, "tenant_type", None) != "platform"
    ):
        raise HTTPException(status_code=403, detail="Platform control-plane access required")
    return _session_id(request)


def _rate_bucket(kind: str, value: str) -> str:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"pilot:{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


async def enforce_pilot_mutation_rate_limit(target_tenant_id: uuid.UUID, session_id: uuid.UUID) -> None:
    """Apply shared fail-closed tenant and durable-session mutation limits."""

    try:
        session_allowed, _ = await _pilot_security_cache.rate_limit_check_shared(
            _rate_bucket("session", str(session_id)),
            PILOT_MUTATION_RATE_LIMIT,
            PILOT_MUTATION_RATE_WINDOW_SECONDS,
        )
        tenant_allowed, _ = await _pilot_security_cache.rate_limit_check_shared(
            _rate_bucket("tenant", str(target_tenant_id)),
            PILOT_MUTATION_RATE_LIMIT,
            PILOT_MUTATION_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Pilot mutation service is temporarily unavailable") from exc
    if not session_allowed or not tenant_allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many pilot mutation requests",
            headers={"Retry-After": str(PILOT_MUTATION_RATE_WINDOW_SECONDS)},
        )
