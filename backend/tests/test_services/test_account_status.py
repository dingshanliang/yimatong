import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.audit import PlatformAuditLog
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.services.organization import set_account_active_status
from app.utils.security import hash_password


async def _build_accounts(db: AsyncSession) -> tuple[Tenant, Account, Account, Role]:
    tenant = Tenant(id=uuid7(), name="状态测试租户", slug=f"status-{uuid7().hex[:8]}")
    db.add(tenant)
    await db.flush()

    organization = Organization(id=uuid7(), tenant_id=tenant.id, name="总部")
    admin_role = Role(id=uuid7(), tenant_id=tenant.id, name="admin", description="管理员")
    operator_role = Role(id=uuid7(), tenant_id=tenant.id, name="operator", description="运营")
    db.add_all([organization, admin_role, operator_role])
    await db.flush()

    actor = Account(
        id=uuid7(),
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="admin@status.test",
        hashed_password=hash_password("Password1"),
        name="管理员",
    )
    target = Account(
        id=uuid7(),
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="operator@status.test",
        hashed_password=hash_password("Password1"),
        name="运营人员",
    )
    db.add_all([actor, target])
    await db.flush()
    await db.execute(account_roles.insert().values(account_id=actor.id, role_id=admin_role.id))
    await db.execute(account_roles.insert().values(account_id=target.id, role_id=operator_role.id))
    await db.commit()
    return tenant, actor, target, admin_role


@pytest.mark.anyio
async def test_disable_account_records_audit_and_increments_auth_version(db: AsyncSession):
    tenant, actor, target, _ = await _build_accounts(db)

    updated = await set_account_active_status(
        db,
        tenant_id=tenant.id,
        actor_id=actor.id,
        account_id=target.id,
        is_active=False,
        reason="员工离职",
    )

    assert updated is not None
    assert updated.is_active is False
    assert updated.auth_version == 1
    audit = (
        await db.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "account_disabled",
                PlatformAuditLog.operator_id == str(actor.id),
            )
        )
    ).scalar_one()
    assert audit.details == {
        "target_account_id": str(target.id),
        "reason": "员工离职",
        "before": "enabled",
        "after": "disabled",
    }


@pytest.mark.anyio
async def test_account_status_change_is_tenant_scoped(db: AsyncSession):
    tenant, actor, target, _ = await _build_accounts(db)

    updated = await set_account_active_status(
        db,
        tenant_id=uuid.uuid4(),
        actor_id=actor.id,
        account_id=target.id,
        is_active=False,
        reason="越权尝试",
    )

    assert updated is None
    assert target.is_active is True
    assert target.tenant_id == tenant.id


@pytest.mark.anyio
async def test_account_cannot_disable_itself(db: AsyncSession):
    tenant, actor, _, _ = await _build_accounts(db)

    with pytest.raises(ValueError, match="不能停用当前登录账户"):
        await set_account_active_status(
            db,
            tenant_id=tenant.id,
            actor_id=actor.id,
            account_id=actor.id,
            is_active=False,
            reason="误操作",
        )


@pytest.mark.anyio
async def test_last_active_admin_cannot_be_disabled(db: AsyncSession):
    tenant, actor, target, admin_role = await _build_accounts(db)
    await db.execute(account_roles.delete().where(account_roles.c.account_id == target.id))
    await db.execute(account_roles.insert().values(account_id=target.id, role_id=admin_role.id))
    actor.is_active = False
    await db.commit()

    with pytest.raises(ValueError, match="最后一个有效管理员"):
        await set_account_active_status(
            db,
            tenant_id=tenant.id,
            actor_id=uuid.uuid4(),
            account_id=target.id,
            is_active=False,
            reason="错误操作",
        )
