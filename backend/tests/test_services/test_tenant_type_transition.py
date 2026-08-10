import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PlatformAuditLog
from app.models.auth_security import AuthSession
from app.models.tenant import Account, AgencyAuthorization, AgencyAuthStatus, Organization, Tenant, TenantType
from app.services.tenant import TenantTypeTransitionConflict, update_tenant
from app.utils.security import hash_password


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("original_type", "next_type"),
    [(TenantType.agency, TenantType.brand), (TenantType.brand, TenantType.agency)],
)
async def test_tenant_type_transition_invalidates_accounts_sessions_and_is_audited(
    db: AsyncSession,
    original_type: TenantType,
    next_type: TenantType,
):
    tenant = Tenant(
        name="身份转换租户",
        slug=f"type-transition-{uuid.uuid4().hex[:8]}",
        tenant_type=original_type,
    )
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="总部")
    db.add(organization)
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email=f"type-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Password1"),
        name="管理员",
        auth_version=4,
    )
    db.add(account)
    await db.flush()
    auth_session = AuthSession(
        id=uuid.uuid4(),
        account_id=account.id,
        tenant_id=tenant.id,
        auth_version=account.auth_version,
        current_refresh_jti=str(uuid.uuid4()),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(auth_session)
    await db.flush()

    transitioned = await update_tenant(
        db,
        tenant.id,
        tenant_type=next_type.value,
        actor_id="platform-admin",
    )

    assert transitioned is not None
    assert transitioned.tenant_type == next_type
    await db.refresh(account)
    await db.refresh(auth_session)
    assert account.auth_version == 5
    assert auth_session.revoked_at is not None
    audit = (
        await db.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.target_tenant_id == str(tenant.id),
                PlatformAuditLog.action == "tenant_type_changed",
            )
        )
    ).scalar_one()
    assert audit.operator_id == "platform-admin"
    assert audit.details == {
        "old_tenant_type": original_type.value,
        "new_tenant_type": next_type.value,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("original_type", "next_type"),
    [(TenantType.agency, TenantType.brand), (TenantType.brand, TenantType.agency)],
)
async def test_active_agency_relationship_rejects_type_transition_without_side_effects_until_revoked(
    db: AsyncSession,
    original_type: TenantType,
    next_type: TenantType,
):
    target = Tenant(
        name="受授权保护租户",
        slug=f"protected-transition-{uuid.uuid4().hex[:8]}",
        tenant_type=original_type,
    )
    counterpart = Tenant(
        name="授权对端租户",
        slug=f"protected-counterpart-{uuid.uuid4().hex[:8]}",
        tenant_type=TenantType.brand if original_type == TenantType.agency else TenantType.agency,
    )
    db.add_all([target, counterpart])
    await db.flush()
    organization = Organization(tenant_id=target.id, name="总部")
    db.add(organization)
    await db.flush()
    account = Account(
        tenant_id=target.id,
        organization_id=organization.id,
        email=f"protected-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Password1"),
        name="管理员",
        auth_version=7,
    )
    db.add(account)
    await db.flush()
    auth_session = AuthSession(
        id=uuid.uuid4(),
        account_id=account.id,
        tenant_id=target.id,
        auth_version=account.auth_version,
        current_refresh_jti=str(uuid.uuid4()),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    authorization = AgencyAuthorization(
        agency_tenant_id=target.id if original_type == TenantType.agency else counterpart.id,
        client_tenant_id=counterpart.id if original_type == TenantType.agency else target.id,
        scope=["products"],
        status=AgencyAuthStatus.active,
    )
    db.add_all([auth_session, authorization])
    await db.flush()

    with pytest.raises(TenantTypeTransitionConflict, match="请先撤销授权"):
        await update_tenant(
            db,
            target.id,
            name="不得部分更新",
            tenant_type=next_type.value,
            actor_id="platform-admin",
        )

    await db.refresh(target)
    await db.refresh(account)
    await db.refresh(auth_session)
    assert target.name == "受授权保护租户"
    assert target.tenant_type == original_type
    assert account.auth_version == 7
    assert auth_session.revoked_at is None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.target_tenant_id == str(target.id),
                PlatformAuditLog.action == "tenant_type_changed",
            )
        )
        == 0
    )

    authorization.status = AgencyAuthStatus.revoked
    authorization.revoked_at = datetime.now(UTC)
    await db.flush()
    transitioned = await update_tenant(
        db,
        target.id,
        tenant_type=next_type.value,
        actor_id="platform-admin",
    )

    assert transitioned is not None
    assert transitioned.tenant_type == next_type
    await db.refresh(account)
    await db.refresh(auth_session)
    assert account.auth_version == 8
    assert auth_session.revoked_at is not None
