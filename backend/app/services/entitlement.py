"""租户套餐运行时状态。

套餐到期是可逆的只读状态，不改变 ``Tenant.status``。平台延长
``plan_expires_at`` 后，下一次请求会立即恢复。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.models.tenant import Tenant

PLAN_EXPIRED_CODE = "TENANT_PLAN_EXPIRED"
PLAN_EXPIRED_DETAIL = "租户套餐已过期，当前仅支持查看；请联系平台续期"
FEATURE_DISABLED_CODE = "TENANT_FEATURE_DISABLED"

# Commercial feature entitlements have one canonical vocabulary.  The
# channel_store spelling shipped in early demo tenants and is read-only
# compatibility; new plan and tenant writes must use channel_portal.
FEATURE_KEYS = frozenset({"ai_assistant", "risk_module", "channel_portal", "white_label", "cash_red_packet"})
LEGACY_FEATURE_ALIASES = {"channel_store": "channel_portal"}


class TenantPlanExpiredError(ForbiddenError):
    """租户套餐已过期，当前请求属于计费或业务写入。"""

    def __init__(self, detail: str = PLAN_EXPIRED_DETAIL):
        super().__init__(detail, error_code=PLAN_EXPIRED_CODE)


class TenantFeatureDisabledError(ForbiddenError):
    """The tenant has not purchased the requested commercial feature."""

    def __init__(self, feature_key: str):
        self.feature_key = feature_key
        super().__init__(
            f"Feature is not enabled for this tenant: {feature_key}",
            error_code=FEATURE_DISABLED_CODE,
        )


def validate_feature_flags(flags: dict | None, *, allow_legacy: bool = False) -> dict[str, bool]:
    """Validate and normalize persisted or incoming feature entitlement JSON."""

    if flags is not None and not isinstance(flags, dict):
        raise ValueError("Feature flags must be an object")
    normalized: dict[str, bool] = {}
    for raw_key, value in (flags or {}).items():
        key = LEGACY_FEATURE_ALIASES.get(raw_key, raw_key) if allow_legacy else raw_key
        if key not in FEATURE_KEYS or (raw_key in LEGACY_FEATURE_ALIASES and not allow_legacy):
            raise ValueError(f"Unsupported feature flag: {raw_key}")
        if not isinstance(value, bool):
            raise ValueError(f"Feature flag {raw_key} must be a boolean")
        # Canonical false must not erase an enabled legacy alias while reading
        # a mixed rollout row.
        normalized[key] = normalized.get(key, False) or value
    return normalized


def enabled_feature_flags(flags: dict | None) -> dict[str, bool]:
    """Return a fail-closed canonical feature map for a persisted tenant row."""

    if not isinstance(flags, dict):
        return {key: False for key in FEATURE_KEYS}
    normalized: dict[str, bool] = {}
    for raw_key, value in (flags or {}).items():
        key = LEGACY_FEATURE_ALIASES.get(raw_key, raw_key)
        # New writes reject malformed values, while reads isolate malformed or
        # unknown historical keys instead of disabling unrelated paid features.
        if key not in FEATURE_KEYS or not isinstance(value, bool):
            continue
        normalized[key] = normalized.get(key, False) or value
    return {key: normalized.get(key, False) for key in FEATURE_KEYS}


def is_feature_enabled(flags: dict | None, feature_key: str) -> bool:
    if feature_key not in FEATURE_KEYS:
        raise ValueError(f"Unsupported feature flag: {feature_key}")
    return enabled_feature_flags(flags)[feature_key]


def is_plan_expired(plan_expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if plan_expires_at is None:
        return False
    expires_at = plan_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= (now or datetime.now(UTC))


async def require_active_plan(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    lock_tenant: bool = False,
) -> Tenant | None:
    """读取（可选锁定）租户，并在套餐过期时稳定地拒绝计费动作。"""

    query = select(Tenant).where(Tenant.id == tenant_id)
    if lock_tenant:
        query = query.with_for_update()
    tenant = (await db.execute(query)).scalar_one_or_none()
    if tenant is not None and is_plan_expired(tenant.plan_expires_at):
        raise TenantPlanExpiredError(PLAN_EXPIRED_DETAIL)
    return tenant


async def require_tenant_feature(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    feature_key: str,
) -> Tenant:
    """Fail closed unless the current tenant owns one canonical paid feature."""

    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None or not is_feature_enabled(tenant.enabled_features, feature_key):
        raise TenantFeatureDisabledError(feature_key)
    return tenant
