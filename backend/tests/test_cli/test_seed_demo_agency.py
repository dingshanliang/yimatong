import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from app.models.audit import PlatformAuditLog
from app.models.plan import TenantQuotaUsage
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    Role,
    Tenant,
    TenantType,
    account_roles,
)
from app.utils.security import verify_password
from scripts.seed_demo import (
    DEMO_AGENCY_EMAIL,
    DEMO_AGENCY_PASSWORD,
    DEMO_AGENCY_SCOPES,
    _ensure_demo_agency_authorization,
    _ensure_demo_agency_identity,
    app,
    settings,
)

runner = CliRunner()


@pytest.mark.anyio
async def test_demo_agency_identity_is_complete_and_idempotent(db: AsyncSession):
    agency_slug = f"unit-demo-agency-{uuid.uuid4().hex[:8]}"

    first_id = await _ensure_demo_agency_identity(db, agency_slug=agency_slug)
    await db.commit()
    second_id = await _ensure_demo_agency_identity(db, agency_slug=agency_slug)
    await db.commit()

    tenant = await db.get(Tenant, first_id)
    account = await db.scalar(select(Account).where(Account.tenant_id == first_id, Account.email == DEMO_AGENCY_EMAIL))
    assert first_id == second_id
    assert tenant is not None and tenant.tenant_type == TenantType.agency
    assert account is not None and account.is_active is True
    assert verify_password(DEMO_AGENCY_PASSWORD, account.hashed_password)
    assert (
        await db.scalar(
            select(func.count())
            .select_from(account_roles)
            .join(
                Role,
                (Role.tenant_id == account_roles.c.tenant_id) & (Role.id == account_roles.c.role_id),
            )
            .where(
                account_roles.c.tenant_id == first_id,
                account_roles.c.account_id == account.id,
                Role.name == "admin",
            )
        )
        == 1
    )
    assert await db.get(TenantQuotaUsage, first_id) is not None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.target_tenant_id == str(first_id),
                PlatformAuditLog.action == "brand_tenant_initialized",
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_demo_agency_replaces_identity_only_residual(db: AsyncSession):
    agency_slug = f"unit-demo-agency-residual-{uuid.uuid4().hex[:8]}"
    residual = Tenant(
        name="残留代运营租户",
        slug=agency_slug,
        tenant_type=TenantType.agency,
        plan="pro",
    )
    db.add(residual)
    await db.commit()
    residual_id = residual.id

    repaired_id = await _ensure_demo_agency_identity(db, agency_slug=agency_slug)
    await db.commit()

    assert repaired_id != residual_id
    assert await db.get(Tenant, residual_id) is None
    account = await db.scalar(
        select(Account).where(Account.tenant_id == repaired_id, Account.email == DEMO_AGENCY_EMAIL)
    )
    assert account is not None and account.is_active is True


@pytest.mark.anyio
async def test_demo_agency_authorization_is_exact_and_idempotent(db: AsyncSession):
    agency = Tenant(
        name="Seed agency",
        slug=f"unit-seed-agency-{uuid.uuid4().hex[:8]}",
        tenant_type=TenantType.agency,
        plan="pro",
    )
    client = Tenant(
        name="Seed client",
        slug=f"unit-seed-client-{uuid.uuid4().hex[:8]}",
        tenant_type=TenantType.brand,
        plan="free",
    )
    db.add_all((agency, client))
    await db.commit()

    first_id = await _ensure_demo_agency_authorization(
        db,
        agency_tenant_id=agency.id,
        client_tenant_id=client.id,
    )
    await db.commit()
    second_id = await _ensure_demo_agency_authorization(
        db,
        agency_tenant_id=agency.id,
        client_tenant_id=client.id,
    )
    await db.commit()

    authorization = await db.get(AgencyAuthorization, first_id)
    assert first_id == second_id
    assert authorization is not None
    assert authorization.scope == DEMO_AGENCY_SCOPES
    assert authorization.expires_at is None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AgencyAuthorization)
            .where(
                AgencyAuthorization.agency_tenant_id == agency.id,
                AgencyAuthorization.client_tenant_id == client.id,
                AgencyAuthorization.status == AgencyAuthStatus.active,
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_demo_agency_authorization_fails_closed_on_conflicting_active_grant(db: AsyncSession):
    agency = Tenant(
        name="Conflicting seed agency",
        slug=f"unit-conflict-agency-{uuid.uuid4().hex[:8]}",
        tenant_type=TenantType.agency,
        plan="pro",
    )
    client = Tenant(
        name="Conflicting seed client",
        slug=f"unit-conflict-client-{uuid.uuid4().hex[:8]}",
        tenant_type=TenantType.brand,
        plan="free",
    )
    db.add_all((agency, client))
    await db.flush()
    conflicting = AgencyAuthorization(
        agency_tenant_id=agency.id,
        client_tenant_id=client.id,
        scope=["pages"],
        status=AgencyAuthStatus.active,
    )
    db.add(conflicting)
    await db.commit()

    with pytest.raises(RuntimeError, match="conflicts with the expected"):
        await _ensure_demo_agency_authorization(
            db,
            agency_tenant_id=agency.id,
            client_tenant_id=client.id,
        )

    assert await db.get(AgencyAuthorization, conflicting.id) is not None


@pytest.mark.parametrize("command", ["generate", "clean", "reset"])
def test_demo_commands_reject_wrong_target_before_db(command: str, monkeypatch: pytest.MonkeyPatch):
    blocked_session = Mock(side_effect=AssertionError("DB session must not open"))
    monkeypatch.setattr("scripts.seed_demo.control_session", blocked_session)
    monkeypatch.setattr("scripts.seed_demo.async_session", blocked_session)

    result = runner.invoke(app, [command, "--target", "customer"])

    assert result.exit_code == 2
    assert "非 demo 目标" in result.output
    blocked_session.assert_not_called()


@pytest.mark.parametrize("command", ["generate", "clean", "reset"])
def test_demo_commands_reject_production_before_db(command: str, monkeypatch: pytest.MonkeyPatch):
    blocked_session = Mock(side_effect=AssertionError("DB session must not open"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("scripts.seed_demo.control_session", blocked_session)
    monkeypatch.setattr("scripts.seed_demo.async_session", blocked_session)

    result = runner.invoke(app, [command, "--target", "demo"])

    assert result.exit_code == 2
    assert "production" in result.output
    blocked_session.assert_not_called()


@pytest.mark.parametrize("command", ["generate", "reset"])
def test_identity_bearing_demo_commands_reject_production_authority_before_db_or_subprocess(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
):
    blocked_session = Mock(side_effect=AssertionError("DB session must not open"))
    blocked_subprocess = Mock(side_effect=AssertionError("child process must not start"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("scripts.seed_demo.control_session", blocked_session)
    monkeypatch.setattr("scripts.seed_demo.async_session", blocked_session)
    monkeypatch.setattr("scripts.seed_demo.subprocess.run", blocked_subprocess)

    result = runner.invoke(app, [command, "--target", "demo", "--allow-production"])

    assert result.exit_code == 2
    assert "内置演示账号" in result.output
    blocked_session.assert_not_called()
    blocked_subprocess.assert_not_called()
