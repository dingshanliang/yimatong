import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.cli import seed as basic_seed
from app.models.audit import PlatformAuditLog
from app.models.member import ConsumerProfile
from app.models.tenant import Account, Organization, Permission, Role, Tenant, account_roles, role_permissions
from app.utils.security import hash_password, verify_password
from scripts import seed_demo


def test_official_demo_seeds_enable_the_risk_lifecycle_they_showcase():
    assert basic_seed.DEMO_ENABLED_FEATURES["risk_module"] is True
    assert seed_demo.DEMO_ENABLED_FEATURES["risk_module"] is True


@pytest.mark.anyio
async def test_basic_and_rich_demo_reconciliation_preserve_channel_portal_roles(db: AsyncSession):
    tenant = Tenant(name="Demo portal identities", slug=f"demo-portal-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="演示默认组织")
    db.add(organization)
    await db.flush()

    await basic_seed._ensure_demo_accounts(db, tenant.id, organization.id)
    await db.flush()
    await seed_demo._ensure_accounts(db, tenant.id, organization.id)
    await db.flush()
    await basic_seed._ensure_demo_accounts(db, tenant.id, organization.id)
    await db.flush()

    rows = (
        await db.execute(
            select(Account.email, Role.name)
            .join(account_roles, account_roles.c.account_id == Account.id)
            .join(Role, Role.id == account_roles.c.role_id)
            .where(Account.tenant_id == tenant.id, Account.email.in_({"dist@demo.com", "store@demo.com"}))
            .order_by(Account.email)
        )
    ).all()
    assert rows == [("dist@demo.com", "distributor"), ("store@demo.com", "store_guide")]


@pytest.mark.anyio
@pytest.mark.parametrize("role_name", ["admin", "operator"])
async def test_rich_demo_role_reconciles_canonical_consumer_permission(db: AsyncSession, role_name: str):
    tenant = Tenant(name="Demo permission repair", slug=f"demo-permission-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    role = Role(tenant_id=tenant.id, name=role_name)
    db.add(role)
    await db.flush()

    reconciled = await seed_demo._ensure_role(db, tenant.id, role_name, "canonical")
    await db.flush()

    granted = await db.scalar(
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .where(
            role_permissions.c.tenant_id == tenant.id,
            role_permissions.c.role_id == reconciled.id,
            Permission.code == "consumer:detail",
        )
    )
    assert granted == "consumer:detail"


@pytest.mark.anyio
async def test_rich_demo_consumers_use_anonymous_authority_without_fabricated_pii(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = Tenant(name="Demo consumer authority", slug=f"demo-consumer-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    authority_calls = 0

    async def create_anonymous(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
        nonlocal authority_calls
        authority_calls += 1
        profile = ConsumerProfile(id=uuid7(), tenant_id=tenant_id)
        session.add(profile)
        await session.flush()
        return {"consumer_id": profile.id, "created_at": profile.created_at}

    monkeypatch.setattr(seed_demo, "create_anonymous_consumer_profile_authority", create_anonymous)

    consumers, consumer_ids = await seed_demo._ensure_consumers(db, tenant.id)

    assert authority_calls == 200
    assert len(consumers) == len(consumer_ids) == 200
    assert all(consumer.nickname is None for consumer in consumers)
    assert all(consumer.phone_hash is None and consumer.phone_ciphertext is None for consumer in consumers)
    assert all(
        consumer.wechat_openid_hash is None and consumer.wechat_openid_ciphertext is None for consumer in consumers
    )
    assert all((consumer.extra_data or {}) == {} for consumer in consumers)
    assert all(consumer.member_level == "normal" and consumer.total_points == 0 for consumer in consumers)


@pytest.mark.anyio
async def test_rich_demo_consumers_repair_partial_population_to_exact_target(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = Tenant(name="Demo consumer retry", slug=f"demo-consumer-retry-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    db.add_all([ConsumerProfile(id=uuid7(), tenant_id=tenant.id) for _index in range(3)])
    await db.flush()
    authority_calls = 0

    async def create_anonymous(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
        nonlocal authority_calls
        authority_calls += 1
        profile = ConsumerProfile(id=uuid7(), tenant_id=tenant_id)
        session.add(profile)
        await session.flush()
        return {"consumer_id": profile.id, "created_at": profile.created_at}

    monkeypatch.setattr(seed_demo, "create_anonymous_consumer_profile_authority", create_anonymous)

    consumers, consumer_ids = await seed_demo._ensure_consumers(db, tenant.id)

    assert authority_calls == 197
    assert len(consumers) == len(consumer_ids) == 200
    assert len(set(consumer_ids)) == 200


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
