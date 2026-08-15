"""Fail-closed admission for public consent and lead mutations."""

import hashlib
import hmac

from fastapi import HTTPException

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable

PUBLIC_CONSUMER_RATE_WINDOW_SECONDS = 60
PUBLIC_CONSUMER_IP_RATE_LIMIT = 30
PUBLIC_CONSUMER_SUBJECT_RATE_LIMIT = 10

_security_cache = AsyncRedisCache(
    prefix="public_consumer_security",
    default_ttl=PUBLIC_CONSUMER_RATE_WINDOW_SECONDS,
)


def _digest(value: str) -> str:
    key = (settings.hmac_pepper or settings.secret_key).encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


async def enforce_public_consumer_admission(client_ip: str, scan_subject: str) -> None:
    """Apply shared IP and credential-subject limits without retaining raw values."""

    try:
        ip_allowed, _ = await _security_cache.rate_limit_check_shared(
            f"ip:{_digest(client_ip)}",
            PUBLIC_CONSUMER_IP_RATE_LIMIT,
            PUBLIC_CONSUMER_RATE_WINDOW_SECONDS,
        )
        subject_allowed, _ = await _security_cache.rate_limit_check_shared(
            f"subject:{_digest(scan_subject)}",
            PUBLIC_CONSUMER_SUBJECT_RATE_LIMIT,
            PUBLIC_CONSUMER_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Consumer service is temporarily unavailable") from exc
    if not ip_allowed or not subject_allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many consumer requests",
            headers={"Retry-After": str(PUBLIC_CONSUMER_RATE_WINDOW_SECONDS)},
        )
