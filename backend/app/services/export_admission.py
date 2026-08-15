"""Fail-closed admission control for expensive prepared exports."""

import hashlib
import hmac
import uuid

from fastapi import HTTPException

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable

EXPORT_RATE_LIMIT_MAX_ATTEMPTS = 10
EXPORT_RATE_LIMIT_WINDOW_SECONDS = 60

_export_rate_cache = AsyncRedisCache(
    prefix="prepared_export_security",
    default_ttl=EXPORT_RATE_LIMIT_WINDOW_SECONDS,
)


async def enforce_export_rate_limit(tenant_id: uuid.UUID, account_id: uuid.UUID) -> None:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"{tenant_id}:{account_id}".encode(), hashlib.sha256).hexdigest()
    try:
        allowed, _ = await _export_rate_cache.rate_limit_check_shared(
            f"principal:{digest}",
            EXPORT_RATE_LIMIT_MAX_ATTEMPTS,
            EXPORT_RATE_LIMIT_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Export service is temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many export requests",
            headers={"Retry-After": str(EXPORT_RATE_LIMIT_WINDOW_SECONDS)},
        )
