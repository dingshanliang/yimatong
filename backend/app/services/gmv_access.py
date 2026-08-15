"""Authentication and abuse-control boundary for external order routes."""

import hashlib
import hmac
import uuid

from fastapi import Depends, HTTPException, Request

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.auth_rbac import require_durable_session, require_permission

GMV_MUTATION_RATE_LIMIT = 10
GMV_MUTATION_RATE_WINDOW_SECONDS = 60
_gmv_security_cache = AsyncRedisCache(prefix="gmv_security", default_ttl=GMV_MUTATION_RATE_WINDOW_SECONDS)


async def require_direct_brand_order_principal(request: Request) -> uuid.UUID:
    """Return the middleware-validated durable session for a direct brand actor."""

    await require_durable_session(request)
    if (
        getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "acting_tenant_id", None)
        or getattr(request.state, "role", None) not in {"admin", "operator"}
    ):
        raise HTTPException(status_code=403, detail="Direct brand order access required")
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="A live login session is required") from exc


def order_dependencies(permission: str) -> list:
    return [Depends(require_direct_brand_order_principal), Depends(require_permission(permission))]


def _rate_bucket(kind: str, value: str) -> str:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"gmv:{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


def authenticated_import_provenance_digest(
    tenant_id: uuid.UUID,
    source_system: str,
    auth_session_id: uuid.UUID,
) -> str:
    """Bind controlled manual import provenance to the durable login family."""

    secret = (settings.hmac_pepper or settings.secret_key).encode()
    return hmac.new(
        secret,
        f"gmv:authenticated-import:{tenant_id}:{source_system}:{auth_session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def enforce_order_mutation_rate_limit(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    source_system: str,
) -> None:
    """Apply shared fail-closed actor and tenant-source limits."""

    try:
        actor_allowed, _ = await _gmv_security_cache.rate_limit_check_shared(
            _rate_bucket("actor", f"{tenant_id}:{actor_id}"),
            GMV_MUTATION_RATE_LIMIT,
            GMV_MUTATION_RATE_WINDOW_SECONDS,
        )
        source_allowed, _ = await _gmv_security_cache.rate_limit_check_shared(
            _rate_bucket("source", f"{tenant_id}:{source_system}"),
            GMV_MUTATION_RATE_LIMIT,
            GMV_MUTATION_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Order admission service is temporarily unavailable") from exc
    if not actor_allowed or not source_allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many order mutation requests",
            headers={"Retry-After": str(GMV_MUTATION_RATE_WINDOW_SECONDS)},
        )
