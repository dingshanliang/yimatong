"""租户套餐运行时状态。

套餐到期是可逆的只读状态，不改变 ``Tenant.status``。平台延长
``plan_expires_at`` 后，下一次请求会立即恢复。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant

PLAN_EXPIRED_CODE = "TENANT_PLAN_EXPIRED"
PLAN_EXPIRED_DETAIL = "租户套餐已过期，当前仅支持查看；请联系平台续期"


class TenantPlanExpiredError(Exception):
    """租户套餐已过期，当前请求属于计费或业务写入。"""


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
