"""Canonical control-plane tenant status transitions."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Account, Tenant, TenantStatus
from app.modules.initial_admin_activation import InitialAdminActivation
from app.services.audit import write_audit_log
from app.services.redis_cache import AsyncRedisCache


class TenantStatusTransitionError(ValueError):
    """The requested tenant status transition is not allowed."""


async def transition_tenant_status(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    target_status: TenantStatus,
    operator_id: str,
    audit_action: str | None = None,
) -> Tenant | None:
    """Apply one locked status transition and all security side effects."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())).scalar_one_or_none()
    if tenant is None:
        return None

    old_status = tenant.status
    if old_status == TenantStatus.terminated and target_status != TenantStatus.terminated:
        raise TenantStatusTransitionError("已终止租户不能恢复")

    if target_status == TenantStatus.terminated:
        await InitialAdminActivation(db, AsyncRedisCache()).cancel_pending(tenant_id=tenant_id)

    if old_status == target_status:
        return tenant

    tenant.status = target_status
    should_revoke_sessions = (
        old_status == TenantStatus.active and target_status != TenantStatus.active
    ) or target_status == TenantStatus.terminated
    if should_revoke_sessions:
        await db.execute(
            update(Account).where(Account.tenant_id == tenant_id).values(auth_version=Account.auth_version + 1)
        )

    await write_audit_log(
        db,
        operator_id=operator_id,
        target_tenant_id=str(tenant_id),
        action=audit_action or f"status_change:{old_status.value}->{target_status.value}",
        resource=f"tenant:{tenant.slug}",
    )
    await db.flush()
    return tenant


async def terminate_tenant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    operator_id: str,
) -> Tenant | None:
    return await transition_tenant_status(
        db,
        tenant_id=tenant_id,
        target_status=TenantStatus.terminated,
        operator_id=operator_id,
        audit_action="delete_tenant",
    )
