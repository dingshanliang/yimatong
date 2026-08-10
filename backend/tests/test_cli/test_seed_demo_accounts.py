import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PlatformAuditLog
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.utils.security import hash_password, verify_password
from scripts import seed_demo


@pytest.mark.anyio
async def test_existing_demo_account_reconciles_security_once_and_name_without_revocation(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = Tenant(name="Demo account test", slug=f"demo-account-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    desired_org = Organization(tenant_id=tenant.id, name="演示默认组织")
    wrong_org = Organization(tenant_id=tenant.id, name="错误组织")
    wrong_role = Role(tenant_id=tenant.id, name="admin")
    db.add_all([desired_org, wrong_org, wrong_role])
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=wrong_org.id,
        email="ops@demo.com",
        hashed_password=hash_password("WrongPassword1"),
        name="错误姓名",
    )
    db.add(account)
    await db.flush()
    await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=wrong_role.id))
    await db.commit()
    account_id = account.id
    revoke = AsyncMock(return_value=1)
    monkeypatch.setattr(seed_demo, "revoke_current_tenant_account_sessions", revoke)

    await seed_demo._ensure_accounts(db, tenant.id, desired_org.id)
    await db.commit()
    first_hash = account.hashed_password

    reconciled = await db.get(Account, account_id)
    assert reconciled is not None
    assert reconciled.name == "活动运营"
    assert reconciled.organization_id == desired_org.id
    assert verify_password("Ops123456", reconciled.hashed_password)
    assert reconciled.auth_version == 1
    desired_role_name = await db.scalar(
        select(Role.name)
        .join(
            account_roles,
            (account_roles.c.tenant_id == Role.tenant_id) & (account_roles.c.role_id == Role.id),
        )
        .where(account_roles.c.tenant_id == tenant.id, account_roles.c.account_id == account_id)
    )
    assert desired_role_name == "operator"
    revoke.assert_awaited_once_with(db, account_id)
    audit = await db.scalar(
        select(PlatformAuditLog).where(
            PlatformAuditLog.action == "account_identity_reconciled",
            PlatformAuditLog.resource == f"account:{account_id}",
        )
    )
    assert audit is not None
    assert audit.details["changed_fields"] == ["name", "organization", "password", "roles"]
    assert audit.details["before"]["organization"]["name"] == "错误组织"
    assert audit.details["after"]["organization"]["name"] == "演示默认组织"

    revoke.reset_mock()
    await seed_demo._ensure_accounts(db, tenant.id, desired_org.id)
    await db.commit()
    stable = await db.get(Account, account_id)
    assert stable is not None and stable.auth_version == 1 and stable.hashed_password == first_hash
    revoke.assert_not_awaited()
    assert (
        await db.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.action == "account_identity_reconciled",
                PlatformAuditLog.resource == f"account:{account_id}",
            )
        )
        == 1
    )

    stable.name = "仅姓名漂移"
    await db.commit()
    revoke.reset_mock()
    await seed_demo._ensure_accounts(db, tenant.id, desired_org.id)
    await db.commit()
    renamed = await db.get(Account, account_id)
    assert renamed is not None and renamed.name == "活动运营" and renamed.auth_version == 1
    revoke.assert_not_awaited()


@pytest.mark.anyio
async def test_demo_account_reconciliation_rolls_back_when_audit_fails(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = Tenant(name="Demo rollback", slug=f"demo-rollback-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="演示默认组织")
    db.add(organization)
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="ops@demo.com",
        hashed_password=hash_password("WrongPassword1"),
        name="活动运营",
    )
    db.add(account)
    await db.commit()
    account_id = account.id
    initial_hash = account.hashed_password

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(seed_demo, "write_audit_log", fail_audit)
    monkeypatch.setattr(seed_demo, "revoke_current_tenant_account_sessions", AsyncMock(return_value=1))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await seed_demo._ensure_accounts(db, tenant.id, organization.id)
    await db.rollback()

    persisted = await db.get(Account, account_id)
    assert persisted is not None
    assert persisted.hashed_password == initial_hash
    assert persisted.auth_version == 0
