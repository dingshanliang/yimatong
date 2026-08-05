import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class QuotaExceededError(Exception):
    pass


# Standard quota keys used across the platform
QUOTA_KEYS = {
    "max_codes",
    "max_campaigns",
    "max_products",
    "max_accounts",
    "max_active_campaigns",
    "max_codes_per_batch",
    "max_scans",
}


async def check_quota_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str,
    model_cls: type,
) -> None:
    """Check if creating one more resource would exceed tenant quota.

    Queries the tenant's current usage and raises QuotaExceededError if the
    quota limit would be exceeded.
    """
    from app.models.tenant import Tenant

    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())).scalar_one_or_none()
    if not tenant or not tenant.quota:
        return
    total = (
        await db.execute(select(func.count()).select_from(model_cls).where(model_cls.tenant_id == tenant_id))
    ).scalar() or 0
    check_quota_incremental(tenant.quota, resource_type, total, 1)


async def check_quota_incremental_locked(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_type: str,
    model_cls: type,
    additional_amount: int = 1,
) -> None:
    """在租户行锁内检查累计配额。

    PostgreSQL 中同一租户的并发计费动作会串行化，因此后一个事务能看到
    前一个事务已提交的用量，避免并发越限。调用方必须在当前事务内完成
    对应资源写入，不能在检查与写入之间提交。
    """

    from app.services.entitlement import require_active_plan

    tenant = await require_active_plan(db, tenant_id, lock_tenant=True)
    if tenant is None:
        return
    current_usage = int(
        (await db.execute(select(func.count()).select_from(model_cls).where(model_cls.tenant_id == tenant_id))).scalar()
        or 0
    )
    check_quota_incremental(tenant.quota, resource_type, current_usage, additional_amount)


def check_quota(quota: dict | None, resource_type: str, amount: int) -> None:
    """Check if a single operation amount exceeds the quota limit.

    Example: check_quota(tenant.quota, "max_codes_per_batch", 1000)
    """
    if not quota:
        return
    limit = quota.get(resource_type)
    if limit is None:
        return
    if limit < 0:
        return
    if amount > limit:
        raise QuotaExceededError(f"Quota exceeded for {resource_type}: requested {amount}, limit {limit}")


def check_quota_incremental(
    quota: dict | None,
    resource_type: str,
    current_usage: int,
    additional_amount: int,
) -> None:
    """Check if current usage + additional amount would exceed quota.

    Example: check_quota_incremental(tenant.quota, "max_campaigns", 5, 1)
    """
    if not quota:
        return
    limit = quota.get(resource_type)
    if limit is None:
        return
    if limit < 0:
        return
    total = current_usage + additional_amount
    if total > limit:
        raise QuotaExceededError(
            f"Quota exceeded for {resource_type}: "
            f"current {current_usage} + additional {additional_amount} = {total}, limit {limit}"
        )
