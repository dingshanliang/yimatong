"""Shared fail-closed admission control for tenant import endpoints."""

import hashlib
import hmac
import uuid

from fastapi import Depends, HTTPException

from app.core.config import settings
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable

IMPORT_RATE_LIMIT_MAX_ATTEMPTS = 10
IMPORT_RATE_LIMIT_WINDOW_SECONDS = 60

_import_rate_cache = AsyncRedisCache(prefix="import_security", default_ttl=IMPORT_RATE_LIMIT_WINDOW_SECONDS)


async def enforce_import_rate_limit(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
) -> None:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"{tenant_id}:{account_id}".encode(), hashlib.sha256).hexdigest()
    try:
        allowed, _ = await _import_rate_cache.rate_limit_check_shared(
            f"principal:{digest}",
            IMPORT_RATE_LIMIT_MAX_ATTEMPTS,
            IMPORT_RATE_LIMIT_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Import service is temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many import requests",
            headers={"Retry-After": str(IMPORT_RATE_LIMIT_WINDOW_SECONDS)},
        )
