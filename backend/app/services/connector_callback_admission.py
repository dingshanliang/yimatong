"""Fail-closed shared admission for unauthenticated connector callbacks."""

import hashlib
import hmac
import uuid

from fastapi import HTTPException, Request

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.client_ip import get_client_ip

CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS = 60
CONNECTOR_CALLBACK_IP_RATE_LIMIT = 60
CONNECTOR_CALLBACK_IDENTITY_RATE_LIMIT = 300
WECOM_CALLBACK_IP_RATE_LIMIT = 60
WECOM_CALLBACK_IDENTITY_RATE_LIMIT = 300

_callback_security_cache = AsyncRedisCache(
    prefix="connector_callback_security",
    default_ttl=CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS,
)


def _digest(value: str) -> str:
    key = (settings.hmac_pepper or settings.secret_key).encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="Callback service is temporarily unavailable")


def _rate_limited() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Too many callback requests",
        headers={"Retry-After": str(CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS)},
    )


async def enforce_connector_callback_ip_admission(request: Request) -> None:
    """Limit public callback probes before connector discovery or secret access."""

    client_ip = get_client_ip(request)
    try:
        allowed, _ = await _callback_security_cache.rate_limit_check_shared(
            f"ip:{_digest(client_ip)}",
            CONNECTOR_CALLBACK_IP_RATE_LIMIT,
            CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise _unavailable(exc) from exc
    if not allowed:
        raise _rate_limited()


async def enforce_connector_callback_identity_admission(tenant_id: uuid.UUID, connector_id: uuid.UUID) -> None:
    """Limit authenticated provider traffic without retaining tenant or connector IDs in Redis keys."""

    try:
        allowed, _ = await _callback_security_cache.rate_limit_check_shared(
            f"connector:{_digest(f'{tenant_id}:{connector_id}')}",
            CONNECTOR_CALLBACK_IDENTITY_RATE_LIMIT,
            CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise _unavailable(exc) from exc
    if not allowed:
        raise _rate_limited()


async def enforce_wecom_callback_ip_admission(request: Request) -> None:
    """Limit public WeCom callbacks before opening a DB session or decrypting payloads."""

    client_ip = get_client_ip(request)
    try:
        allowed, _ = await _callback_security_cache.rate_limit_check_shared(
            f"wecom-ip:{_digest(client_ip)}",
            WECOM_CALLBACK_IP_RATE_LIMIT,
            CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise _unavailable(exc) from exc
    if not allowed:
        raise _rate_limited()


async def enforce_wecom_callback_identity_admission(tenant_id: uuid.UUID, connector_id: uuid.UUID) -> None:
    """Limit signed WeCom callback traffic on the exact tenant and connector."""

    try:
        allowed, _ = await _callback_security_cache.rate_limit_check_shared(
            f"wecom-connector:{_digest(f'{tenant_id}:{connector_id}')}",
            WECOM_CALLBACK_IDENTITY_RATE_LIMIT,
            CONNECTOR_CALLBACK_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise _unavailable(exc) from exc
    if not allowed:
        raise _rate_limited()
