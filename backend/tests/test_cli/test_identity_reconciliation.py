import uuid
from unittest.mock import AsyncMock

import pytest
import typer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from app.cli.baseline import _ensure_runtime_admin, _TenantSeedRef, app, build_sync, settings
from app.models.audit import PlatformAuditLog
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.utils.security import hash_password, verify_password

runner = CliRunner()


def test_baseline_build_rejects_wrong_target_before_db(monkeypatch: pytest.MonkeyPatch):
    build_dataset = AsyncMock()
    monkeypatch.setattr("app.cli.baseline._build_baseline_dataset", build_dataset)

    result = runner.invoke(app, ["build", "--target", "customer"])

    assert result.exit_code == 2
    assert "非 baseline-base 目标" in result.output
    build_dataset.assert_not_awaited()


def test_baseline_build_rejects_production_before_db(monkeypatch: pytest.MonkeyPatch):
    build_dataset = AsyncMock()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.baseline._build_baseline_dataset", build_dataset)

    result = runner.invoke(app, ["build", "--target", "baseline-base"])

    assert result.exit_code == 2
    assert "production" in result.output
    build_dataset.assert_not_awaited()


def test_baseline_build_rejects_explicit_production_authority_before_db(monkeypatch: pytest.MonkeyPatch):
    build_dataset = AsyncMock()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.baseline._build_baseline_dataset", build_dataset)

    result = runner.invoke(app, ["build", "--target", "baseline-base", "--allow-production"])

    assert result.exit_code == 2
    assert "内置账号密码" in result.output
    build_dataset.assert_not_awaited()


def test_baseline_build_sync_alias_rejects_production_before_db(monkeypatch: pytest.MonkeyPatch):
    build_dataset = AsyncMock()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.baseline._build_baseline_dataset", build_dataset)

    with pytest.raises(typer.Exit) as error:
        build_sync("sqlite+aiosqlite://")

    assert error.value.exit_code == 2
    build_dataset.assert_not_awaited()


@pytest.mark.anyio
async def test_baseline_new_admin_does_not_lazy_load_unpersisted_roles(db: AsyncSession):
    tenant = Tenant(name="New baseline", slug=f"new-baseline-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    tenant_ref = _TenantSeedRef(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        admin_email="new-admin@identity.local",
        admin_name="新管理员",
        admin_password="BaselinePassword1",
    )

    account = await _ensure_runtime_admin(db, tenant, tenant_ref)
    await db.flush()

    assert account.email == tenant_ref.admin_email
    assert account.auth_version == 0
    assert (
        await db.scalar(select(func.count()).select_from(account_roles).where(account_roles.c.account_id == account.id))
        == 1
    )


@pytest.mark.anyio
async def test_baseline_identity_reconciliation_invalidates_once_and_rerun_is_stable(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = Tenant(name="Baseline identity", slug=f"baseline-identity-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="默认组织")
    viewer = Role(tenant_id=tenant.id, name="viewer")
    db.add_all([organization, viewer])
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="admin@identity.local",
        hashed_password=hash_password("OldPassword1"),
        name="旧名称",
    )
    db.add(account)
    await db.flush()
    await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=viewer.id))
    await db.commit()
    tenant_ref = _TenantSeedRef(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        admin_email=account.email,
        admin_name="基准管理员",
        admin_password="BaselinePassword1",
    )

    reconciled = await _ensure_runtime_admin(db, tenant, tenant_ref)
    await db.commit()
    first_hash = reconciled.hashed_password

    assert reconciled.auth_version == 1
    assert verify_password(tenant_ref.admin_password, first_hash)
    audit_count = await db.scalar(
        select(func.count())
        .select_from(PlatformAuditLog)
        .where(PlatformAuditLog.action == "account_identity_reconciled")
    )
    assert audit_count == 1

    rerun = await _ensure_runtime_admin(db, tenant, tenant_ref)
    await db.commit()

    assert rerun.auth_version == 1
    assert rerun.hashed_password == first_hash
    audit_count = await db.scalar(
        select(func.count())
        .select_from(PlatformAuditLog)
        .where(PlatformAuditLog.action == "account_identity_reconciled")
    )
    assert audit_count == 1

    rerun.name = "仅显示名漂移"
    await db.commit()
    revoke = AsyncMock()
    monkeypatch.setattr("app.cli.baseline.revoke_current_tenant_account_sessions", revoke)

    renamed = await _ensure_runtime_admin(db, tenant, tenant_ref)
    await db.commit()

    assert renamed.name == tenant_ref.admin_name
    assert renamed.auth_version == 1
    revoke.assert_not_awaited()
    latest_audit = await db.scalar(
        select(PlatformAuditLog)
        .where(PlatformAuditLog.action == "account_identity_reconciled")
        .order_by(PlatformAuditLog.timestamp.desc(), PlatformAuditLog.id.desc())
    )
    assert latest_audit is not None
    assert latest_audit.details["changed_fields"] == ["name"]
    assert latest_audit.details["before"]["name"] == "仅显示名漂移"
    assert latest_audit.details["after"]["name"] == tenant_ref.admin_name
    assert latest_audit.details["before"]["organization"]["name"] == f"{tenant.name} 默认组织"
    assert latest_audit.details["after"]["roles"][0]["name"] == "admin"
