"""Fail-closed admission control for external takeover probes."""

import hashlib
import hmac
import uuid

from fastapi import Depends, HTTPException

from app.core.config import settings
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable

TAKEOVER_PROBE_RATE_LIMIT_MAX_ATTEMPTS = 10
TAKEOVER_PROBE_RATE_LIMIT_WINDOW_SECONDS = 60

_takeover_probe_rate_cache = AsyncRedisCache(
    prefix="takeover_probe_security",
    default_ttl=TAKEOVER_PROBE_RATE_LIMIT_WINDOW_SECONDS,
)


async def enforce_takeover_probe_rate_limit(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
) -> None:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"{tenant_id}:{account_id}".encode(), hashlib.sha256).hexdigest()
    try:
        allowed, _ = await _takeover_probe_rate_cache.rate_limit_check_shared(
            f"principal:{digest}",
            TAKEOVER_PROBE_RATE_LIMIT_MAX_ATTEMPTS,
            TAKEOVER_PROBE_RATE_LIMIT_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Takeover probe service is temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many takeover probe requests",
            headers={"Retry-After": str(TAKEOVER_PROBE_RATE_LIMIT_WINDOW_SECONDS)},
        )
