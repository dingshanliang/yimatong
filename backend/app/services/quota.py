import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, QuotaExceededError
from app.models.plan import QuotaRolloutPhase, QuotaRolloutState, TenantQuotaUsage


class InvalidQuotaConfigurationError(ValueError):
    """A quota document or requested quota key is outside the canonical contract."""


class QuotaTenantNotFoundError(LookupError):
    """Quota reservation was requested for a tenant that does not exist."""


class CumulativeQuotaKey(StrEnum):
    MAX_CODES = "max_codes"
    MAX_SCANS = "max_scans"
    MAX_CAMPAIGNS = "max_campaigns"
    MAX_PRODUCTS = "max_products"
    MAX_ACCOUNTS = "max_accounts"


class OperationQuotaKey(StrEnum):
    """Typed, single-operation limits that do not represent cumulative usage."""

    MAX_CODES_PER_BATCH = "max_codes_per_batch"


@dataclass(frozen=True, slots=True)
class CumulativeQuotaSpec:
    usage_attribute: str


CUMULATIVE_QUOTA_REGISTRY: dict[CumulativeQuotaKey, CumulativeQuotaSpec] = {
    CumulativeQuotaKey.MAX_CODES: CumulativeQuotaSpec("codes"),
    CumulativeQuotaKey.MAX_SCANS: CumulativeQuotaSpec("scans"),
    CumulativeQuotaKey.MAX_CAMPAIGNS: CumulativeQuotaSpec("campaigns"),
    CumulativeQuotaKey.MAX_PRODUCTS: CumulativeQuotaSpec("products"),
    CumulativeQuotaKey.MAX_ACCOUNTS: CumulativeQuotaSpec("accounts"),
}
OPERATION_QUOTA_KEYS = frozenset(OperationQuotaKey)
QUOTA_KEYS = frozenset(key.value for key in (*CUMULATIVE_QUOTA_REGISTRY, *OPERATION_QUOTA_KEYS))
QUOTA_RECONCILIATION_SOURCE_REVISION = "93f7b66a0ec6"
QUOTA_ROLLOUT_SINGLETON_ID = 1
# Stable signed-bigint namespace for the transaction-scoped global rollout
# lock. Advisory locking lets the restricted runtime role participate without
# receiving UPDATE privilege on control-plane state.
QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 0x594D5451554F5441


class QuotaReconciliationGateError(RuntimeError):
    """The operator has not confirmed that legacy writers are drained."""


@dataclass(frozen=True, slots=True)
class QuotaEnforcementReadiness:
    total_tenants: int
    ready_tenants: int
    expected_source_revision: str
    global_phase: QuotaRolloutPhase | None
    global_source_revision: str | None

    @property
    def global_active_current(self) -> bool:
        return (
            self.global_phase is QuotaRolloutPhase.active
            and self.global_source_revision == self.expected_source_revision
        )

    @property
    def ready(self) -> bool:
        return self.global_active_current and self.ready_tenants == self.total_tenants


def is_current_quota_epoch_active(state: QuotaRolloutState | None) -> bool:
    return bool(
        state is not None
        and state.phase is QuotaRolloutPhase.active
        and state.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
        and state.activated_at is not None
        and state.activated_by
    )


def is_quota_usage_effectively_ready(
    usage: TenantQuotaUsage | None,
    state: QuotaRolloutState | None,
) -> bool:
    return bool(
        is_current_quota_epoch_active(state)
        and usage is not None
        and usage.enforcement_ready
        and usage.reconciled_at is not None
        and usage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
    )


async def lock_quota_rollout_state(
    db: AsyncSession,
    *,
    write: bool = False,
) -> QuotaRolloutState | None:
    """Lock the global epoch before any tenant lock in quota-sensitive flows.

    PostgreSQL requires UPDATE privilege even for ``SELECT ... FOR SHARE``.
    Runtime traffic intentionally has only SELECT on this control table, so a
    transaction-scoped advisory shared/exclusive lock is the cross-role mutex;
    control writers additionally row-lock the singleton before mutating it.
    """

    if db.get_bind().dialect.name == "postgresql":
        lock_function = func.pg_advisory_xact_lock if write else func.pg_advisory_xact_lock_shared
        await db.execute(select(lock_function(QUOTA_ROLLOUT_ADVISORY_LOCK_KEY)))
    stmt = select(QuotaRolloutState).where(QuotaRolloutState.id == QUOTA_ROLLOUT_SINGLETON_ID)
    if write:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


def _operator_provenance(operator: str) -> str:
    normalized = operator.strip() if isinstance(operator, str) else ""
    if not normalized or len(normalized) > 128:
        raise QuotaReconciliationGateError("Quota rollout operator must contain 1-128 characters")
    return normalized


def validate_quota_config(quota: dict | None) -> dict[str, int]:
    """Validate and normalize the complete quota document.

    Missing keys mean no configured limit. ``-1`` is the only unlimited
    sentinel. Booleans are deliberately rejected even though Python treats
    them as integers.
    """

    if quota is None:
        return {}
    if not isinstance(quota, dict):
        raise InvalidQuotaConfigurationError("Quota configuration must be an object")

    unknown = sorted(str(key) for key in quota if key not in QUOTA_KEYS)
    if unknown:
        raise InvalidQuotaConfigurationError(f"Unknown quota keys: {', '.join(unknown)}")

    normalized: dict[str, int] = {}
    for key, value in quota.items():
        if type(value) is not int:  # noqa: E721 - bool must not pass as an integer quota
            raise InvalidQuotaConfigurationError(f"Quota {key} must be an integer")
        if value < -1:
            raise InvalidQuotaConfigurationError(f"Quota {key} must be -1 or greater")
        normalized[str(key)] = value
    return normalized


def _cumulative_key(resource_type: str | CumulativeQuotaKey) -> CumulativeQuotaKey:
    try:
        return CumulativeQuotaKey(resource_type)
    except ValueError as exc:
        raise InvalidQuotaConfigurationError(f"Unknown cumulative quota key: {resource_type}") from exc


def _validate_amount(amount: int, *, allow_zero: bool = True) -> int:
    if type(amount) is not int:  # noqa: E721 - bool must not pass as an integer amount
        raise InvalidQuotaConfigurationError("Quota amount must be an integer")
    minimum = 0 if allow_zero else 1
    if amount < minimum:
        raise InvalidQuotaConfigurationError(f"Quota amount must be at least {minimum}")
    return amount


async def _load_or_create_usage(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    bootstrap_key: CumulativeQuotaKey | None = None,
    bootstrap_model_cls: type | None = None,
) -> TenantQuotaUsage:
    usage = await db.get(TenantQuotaUsage, tenant_id)
    if usage is not None:
        return usage

    usage = TenantQuotaUsage(tenant_id=tenant_id)
    if bootstrap_key is not None and bootstrap_model_cls is not None:
        spec = CUMULATIVE_QUOTA_REGISTRY[bootstrap_key]
        authoritative_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(bootstrap_model_cls)
                    .where(bootstrap_model_cls.tenant_id == tenant_id)
                )
            )
            or 0
        )
        setattr(usage, spec.usage_attribute, authoritative_count)
    db.add(usage)
    await db.flush()
    return usage


async def _authoritative_bootstrap_count(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    model_cls: type | None,
) -> int:
    if model_cls is None:
        return 0
    return int(
        (await db.scalar(select(func.count()).select_from(model_cls).where(model_cls.tenant_id == tenant_id))) or 0
    )


async def reserve_quota(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str | CumulativeQuotaKey,
    additional_amount: int = 1,
) -> TenantQuotaUsage:
    """Reserve cumulative quota in the caller's transaction.

    The tenant row lock serializes all reservations for one tenant. The usage
    update is part of the caller's transaction, so a rollback automatically
    releases it together with the business write.
    """

    return await _reserve_quota(db, tenant_id, resource_type, additional_amount)


async def _reserve_quota(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str | CumulativeQuotaKey,
    additional_amount: int,
    *,
    bootstrap_model_cls: type | None = None,
) -> TenantQuotaUsage:
    from app.services.entitlement import PLAN_EXPIRED_CODE, TenantPlanExpiredError, require_active_plan

    key = _cumulative_key(resource_type)
    amount = _validate_amount(additional_amount)
    rollout_state = await lock_quota_rollout_state(db)
    try:
        tenant = await require_active_plan(db, tenant_id, lock_tenant=True)
    except TenantPlanExpiredError as exc:
        raise ForbiddenError(str(exc), error_code=PLAN_EXPIRED_CODE) from exc
    if tenant is None:
        raise QuotaTenantNotFoundError(f"Tenant not found: {tenant_id}")

    quota = validate_quota_config(tenant.quota)
    spec = CUMULATIVE_QUOTA_REGISTRY[key]
    usage = await db.get(TenantQuotaUsage, tenant_id)
    current_usage = (
        int(getattr(usage, spec.usage_attribute))
        if usage is not None
        else await _authoritative_bootstrap_count(db, tenant_id, bootstrap_model_cls)
    )
    # During the mixed-binary bridge window the derived counter may be stale:
    # new code must maintain it, but must not reject valid writes from a value
    # that an old writer could have failed to update. Enforcement starts only
    # after the operator-gated authoritative reconciliation marks this tenant.
    if is_quota_usage_effectively_ready(usage, rollout_state):
        check_quota_incremental(quota, key, current_usage, amount)
    if usage is None:
        usage = TenantQuotaUsage(tenant_id=tenant_id)
        db.add(usage)
    setattr(usage, spec.usage_attribute, current_usage + amount)
    await db.flush()
    return usage


async def release_quota(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str | CumulativeQuotaKey,
    amount: int = 1,
) -> TenantQuotaUsage:
    """Release current-resource usage in the caller's transaction."""

    from app.models.tenant import Tenant

    key = _cumulative_key(resource_type)
    released = _validate_amount(amount)
    await lock_quota_rollout_state(db)
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise QuotaTenantNotFoundError(f"Tenant not found: {tenant_id}")
    usage = await _load_or_create_usage(db, tenant_id)
    spec = CUMULATIVE_QUOTA_REGISTRY[key]
    current_usage = int(getattr(usage, spec.usage_attribute))
    setattr(usage, spec.usage_attribute, max(0, current_usage - released))
    await db.flush()
    return usage


async def reconcile_quota_usage_from_authoritative_rows(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    old_writers_drained: bool = False,
    source_revision: str = QUOTA_RECONCILIATION_SOURCE_REVISION,
) -> TenantQuotaUsage:
    """Rebuild and enable one tenant after the legacy-writer deployment gate.

    The tenant lock is shared with every canonical reservation. Therefore the
    authoritative recount and readiness marker form one linearizable state
    transition: a concurrent new writer is counted either by the recount or by
    its subsequent counter increment, never lost between the two.
    """

    if not old_writers_drained:
        raise QuotaReconciliationGateError(
            "Quota reconciliation requires explicit confirmation that legacy writers are drained"
        )
    normalized_revision = source_revision.strip() if isinstance(source_revision, str) else ""
    if normalized_revision != QUOTA_RECONCILIATION_SOURCE_REVISION:
        raise QuotaReconciliationGateError(
            "Quota reconciliation source revision does not match the current enforcement epoch"
        )

    from app.models.campaign import Campaign
    from app.models.code import CodeItem
    from app.models.product import Product
    from app.models.scan import ScanEvent
    from app.models.tenant import Account, Tenant

    rollout_state = await lock_quota_rollout_state(db)
    if (
        rollout_state is None
        or rollout_state.source_revision != QUOTA_RECONCILIATION_SOURCE_REVISION
        or rollout_state.phase not in {QuotaRolloutPhase.drained, QuotaRolloutPhase.active}
    ):
        raise QuotaReconciliationGateError(
            "Quota rollout epoch must be durably marked drained before tenant reconciliation"
        )

    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise QuotaTenantNotFoundError(f"Tenant not found: {tenant_id}")

    model_by_key = {
        CumulativeQuotaKey.MAX_CODES: CodeItem,
        CumulativeQuotaKey.MAX_SCANS: ScanEvent,
        CumulativeQuotaKey.MAX_CAMPAIGNS: Campaign,
        CumulativeQuotaKey.MAX_PRODUCTS: Product,
        CumulativeQuotaKey.MAX_ACCOUNTS: Account,
    }
    usage = await _load_or_create_usage(db, tenant_id)
    for key, model_cls in model_by_key.items():
        count = int(
            (await db.scalar(select(func.count()).select_from(model_cls).where(model_cls.tenant_id == tenant_id))) or 0
        )
        setattr(usage, CUMULATIVE_QUOTA_REGISTRY[key].usage_attribute, count)
    usage.reconciled_at = datetime.now(UTC)
    usage.source_revision = normalized_revision
    usage.enforcement_ready = True
    await db.flush()
    return usage


async def refresh_quota_usage_from_authoritative_rows(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> TenantQuotaUsage:
    """Refresh counters for seed/repair without bypassing the global epoch."""

    from app.models.campaign import Campaign
    from app.models.code import CodeItem
    from app.models.product import Product
    from app.models.scan import ScanEvent
    from app.models.tenant import Account, Tenant

    rollout_state = await lock_quota_rollout_state(db)
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise QuotaTenantNotFoundError(f"Tenant not found: {tenant_id}")
    usage = await _load_or_create_usage(db, tenant_id)
    model_by_key = {
        CumulativeQuotaKey.MAX_CODES: CodeItem,
        CumulativeQuotaKey.MAX_SCANS: ScanEvent,
        CumulativeQuotaKey.MAX_CAMPAIGNS: Campaign,
        CumulativeQuotaKey.MAX_PRODUCTS: Product,
        CumulativeQuotaKey.MAX_ACCOUNTS: Account,
    }
    for key, model_cls in model_by_key.items():
        count = int(
            (await db.scalar(select(func.count()).select_from(model_cls).where(model_cls.tenant_id == tenant_id))) or 0
        )
        setattr(usage, CUMULATIVE_QUOTA_REGISTRY[key].usage_attribute, count)
    if is_current_quota_epoch_active(rollout_state):
        usage.reconciled_at = datetime.now(UTC)
        usage.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
        usage.enforcement_ready = True
    else:
        usage.reconciled_at = None
        usage.source_revision = None
        usage.enforcement_ready = False
    await db.flush()
    return usage


async def begin_current_quota_rollout_epoch(
    db: AsyncSession,
    *,
    old_writers_drained: bool,
    operator: str,
) -> QuotaRolloutState:
    """Durably record the old-writer drain gate for the current epoch."""

    if not old_writers_drained:
        raise QuotaReconciliationGateError(
            "Quota rollout requires explicit confirmation that legacy writers are drained"
        )
    actor = _operator_provenance(operator)
    state = await lock_quota_rollout_state(db, write=True)
    if state is None:
        raise QuotaReconciliationGateError("Quota rollout singleton is missing")
    if state.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION and state.phase is QuotaRolloutPhase.active:
        return state
    now = datetime.now(UTC)
    if state.source_revision != QUOTA_RECONCILIATION_SOURCE_REVISION:
        state.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
        state.started_at = now
    state.phase = QuotaRolloutPhase.drained
    state.drained_at = now
    state.drained_by = actor
    state.activated_at = None
    state.activated_by = None
    await db.flush()
    return state


async def activate_current_quota_rollout_epoch(
    db: AsyncSession,
    *,
    operator: str,
) -> QuotaEnforcementReadiness:
    """Activate only while holding the singleton exclusively and all tenants are current-ready."""

    actor = _operator_provenance(operator)
    state = await lock_quota_rollout_state(db, write=True)
    if (
        state is None
        or state.source_revision != QUOTA_RECONCILIATION_SOURCE_REVISION
        or state.phase not in {QuotaRolloutPhase.drained, QuotaRolloutPhase.active}
    ):
        raise QuotaReconciliationGateError("Current quota rollout epoch is not durably drained")
    readiness = await _quota_readiness_counts(db, state)
    if readiness.ready_tenants != readiness.total_tenants:
        return readiness
    if state.phase is not QuotaRolloutPhase.active:
        state.phase = QuotaRolloutPhase.active
        state.activated_at = datetime.now(UTC)
        state.activated_by = actor
        await db.flush()
    return await _quota_readiness_counts(db, state)


async def _quota_readiness_counts(
    db: AsyncSession,
    state: QuotaRolloutState | None,
) -> QuotaEnforcementReadiness:
    from app.models.tenant import Tenant

    total_tenants = int((await db.scalar(select(func.count()).select_from(Tenant))) or 0)
    ready_tenants = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Tenant)
                .join(TenantQuotaUsage, TenantQuotaUsage.tenant_id == Tenant.id)
                .where(
                    TenantQuotaUsage.enforcement_ready.is_(True),
                    TenantQuotaUsage.reconciled_at.is_not(None),
                    TenantQuotaUsage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION,
                )
            )
        )
        or 0
    )
    return QuotaEnforcementReadiness(
        total_tenants=total_tenants,
        ready_tenants=ready_tenants,
        expected_source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
        global_phase=state.phase if state is not None else None,
        global_source_revision=state.source_revision if state is not None else None,
    )


async def get_quota_enforcement_readiness(
    db: AsyncSession,
    *,
    source_revision: str = QUOTA_RECONCILIATION_SOURCE_REVISION,
) -> QuotaEnforcementReadiness:
    """Return the deployment gate state across every tenant."""

    normalized_revision = source_revision.strip() if isinstance(source_revision, str) else ""
    if normalized_revision != QUOTA_RECONCILIATION_SOURCE_REVISION:
        raise QuotaReconciliationGateError("Quota readiness revision does not match the current enforcement epoch")
    state = await lock_quota_rollout_state(db)
    return await _quota_readiness_counts(db, state)


async def check_quota_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str,
    model_cls: type,
) -> None:
    """Compatibility seam: reserve one unit using the canonical usage state."""

    await _reserve_quota(db, tenant_id, resource_type, 1, bootstrap_model_cls=model_cls)


async def check_quota_incremental_locked(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str,
    model_cls: type,
    additional_amount: int = 1,
) -> None:
    """Compatibility seam for callers migrating to :func:`reserve_quota`."""

    await _reserve_quota(
        db,
        tenant_id,
        resource_type,
        additional_amount,
        bootstrap_model_cls=model_cls,
    )


def check_quota(
    quota: dict | None,
    resource_type: str | CumulativeQuotaKey | OperationQuotaKey,
    amount: int,
) -> None:
    """Check if a single operation amount exceeds the quota limit.

    Example: check_quota(tenant.quota, "max_codes_per_batch", 1000)
    """
    normalized = validate_quota_config(quota)
    key = str(resource_type)
    if key not in QUOTA_KEYS:
        raise InvalidQuotaConfigurationError(f"Unknown quota key: {key}")
    requested = _validate_amount(amount)
    limit = normalized.get(key)
    if limit is None:
        return
    if limit < 0:
        return
    if requested > limit:
        raise QuotaExceededError(f"Quota exceeded for {key}: requested {requested}, limit {limit}")


def check_quota_incremental(
    quota: dict | None,
    resource_type: str | CumulativeQuotaKey,
    current_usage: int,
    additional_amount: int,
) -> None:
    """Check if current usage + additional amount would exceed quota.

    Example: check_quota_incremental(tenant.quota, "max_campaigns", 5, 1)
    """
    normalized = validate_quota_config(quota)
    key = _cumulative_key(resource_type)
    current = _validate_amount(current_usage)
    additional = _validate_amount(additional_amount)
    limit = normalized.get(key.value)
    if limit is None:
        return
    if limit < 0:
        return
    total = current + additional
    if total > limit:
        raise QuotaExceededError(
            f"Quota exceeded for {key.value}: current {current} + additional {additional} = {total}, limit {limit}"
        )
