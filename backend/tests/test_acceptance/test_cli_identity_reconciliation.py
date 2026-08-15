from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.cli import baseline as baseline_cli
from app.cli import seed
from app.cli.baseline import BASELINE_ADMIN_EMAIL, BASELINE_TENANT_SLUG
from app.models.audit import PlatformAuditLog
from app.models.auth_security import AuthSession
from app.models.plan import TenantQuotaUsage
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.services.quota import lock_quota_rollout_state
from app.utils.security import hash_password, verify_password
from scripts import seed_demo as rich_seed
from tests.test_acceptance.conftest import seed_baseline

pytestmark = pytest.mark.acceptance

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _runtime_url(owner_url: str) -> str:
    return owner_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _cli_environment(owner_url: str, *, production: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "database_url": _runtime_url(owner_url),
            "control_database_url": owner_url,
            "migration_database_url": owner_url,
        }
    )
    if production:
        env.update(
            {
                "environment": "production",
                "secret_key": "production-secret-key-9dd2177f7d104205a14b93e6",
                "HMAC_PEPPER": "production-hmac-pepper-901bc458e0644e728b3d1475",
                "IP_HASH_SECRET": "production-ip-secret-f0ee260ac501437990b78ec1",
                "ADMIN_PUBLIC_URL": "https://admin.example.test",
                "PLATFORM_PUBLIC_URL": "https://platform.example.test",
                "COOKIE_SECURE": "true",
            }
        )
    return env


def _run_cli(owner_url: str, *arguments: str, production: bool = False, succeeds: bool = True) -> None:
    result = subprocess.run(
        ["uv", "run", "python", "-m", "app.cli", *arguments],
        cwd=BACKEND_DIR,
        env=_cli_environment(owner_url, production=production),
        capture_output=True,
        text=True,
        check=False,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 2, result.stdout + result.stderr


def _run_seed_demo_script(
    owner_url: str,
    *arguments: str,
    production: bool = False,
    succeeds: bool = True,
) -> None:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/seed_demo.py", *arguments],
        cwd=BACKEND_DIR,
        env=_cli_environment(owner_url, production=production),
        capture_output=True,
        text=True,
        check=False,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 2, result.stdout + result.stderr


async def _create_active_session(
    db: AsyncSession,
    account: Account,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    db.add(
        AuthSession(
            id=session_id,
            account_id=account.id,
            tenant_id=account.tenant_id,
            auth_version=account.auth_version,
            current_refresh_jti=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db.flush()
    return session_id


async def _assert_cli_lifecycle_actor_and_cleanup(
    db: AsyncSession,
    *,
    tenant_slug: str,
    admin_email: str,
) -> None:
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
    assert tenant is not None
    admin = await db.scalar(select(Account).where(Account.tenant_id == tenant.id, Account.email == admin_email))
    assert admin is not None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.tenant_id == tenant.id,
                AuthSession.current_refresh_jti.like("cli-%"),
            )
        )
        == 0
    )
    lifecycle_operators = set(
        (
            await db.scalars(
                select(PlatformAuditLog.operator_id).where(
                    PlatformAuditLog.target_tenant_id == str(tenant.id),
                    PlatformAuditLog.action == "code_activate",
                )
            )
        ).all()
    )
    assert lifecycle_operators == {str(admin.id)}


async def test_seed_all_repairs_partial_tenant_with_requested_admin_and_keeps_policy_seed_idempotent(
    migrated_pg_url: str,
) -> None:
    slug = f"partial-demo-{uuid.uuid4().hex[:8]}"
    requested_email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            residual = Tenant(name="Partial demo tenant", slug=slug, plan="free")
            db.add(residual)
            await db.flush()
            tenant_id = residual.id

        arguments = (
            "all",
            "--slug",
            slug,
            "--admin-email",
            f" {requested_email.upper()} ",
            "--admin-name",
            "恢复管理员",
            "--admin-password",
            "RecoveryPass123",
            "--allow-non-demo-target",
        )
        await asyncio.to_thread(_run_cli, migrated_pg_url, *arguments)
        await asyncio.to_thread(_run_cli, migrated_pg_url, *arguments)

        async with owner_factory() as db:
            admin = await db.scalar(
                select(Account)
                .options(selectinload(Account.roles))
                .where(Account.tenant_id == tenant_id, Account.email == requested_email)
            )
            assert admin is not None
            assert admin.name == "恢复管理员"
            assert verify_password("RecoveryPass123", admin.hashed_password)
            assert [role.name for role in admin.roles] == ["admin"]
            assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)) == 5
            assert not await db.scalar(
                select(Account.id).where(Account.tenant_id == tenant_id, Account.email == "admin@demo.com")
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM consumer_consent_policies WHERE tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == 3
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM consumer_consent_policy_current WHERE tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == 3
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(
                        AuthSession.tenant_id == tenant_id,
                        AuthSession.current_refresh_jti.like("cli-%"),
                    )
                )
                == 0
            )
    finally:
        await owner_engine.dispose()


async def test_demo_admin_repair_and_account_quota_refresh_share_one_retryable_transaction(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = f"partial-quota-{uuid.uuid4().hex[:8]}"
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            residual = Tenant(name="Partial quota tenant", slug=slug, plan="free")
            db.add(residual)
            await db.flush()
            tenant_id = residual.id

        real_refresh = seed.refresh_quota_usage_from_authoritative_rows

        async def fail_refresh(*_args, **_kwargs):
            raise RuntimeError("quota refresh unavailable")

        monkeypatch.setattr(seed, "async_session", runtime_factory)
        admin_id = await seed._ensure_committed_demo_admin(
            tenant_id,
            admin_email="owner@example.com",
            admin_name="恢复管理员",
            admin_password="RecoveryPass123",
        )

        async with owner_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)) == 1
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 1
            assert await db.get(Account, admin_id) is not None

        monkeypatch.setattr(seed, "refresh_quota_usage_from_authoritative_rows", fail_refresh)
        with pytest.raises(RuntimeError, match="quota refresh unavailable"):
            async with runtime_factory() as db:
                await seed.lock_quota_rollout_state(db)
                tenant = await seed._open_tenant_scope(db, tenant_id)
                organization = await seed._get_default_org(db, tenant_id)
                await seed._ensure_demo_accounts(
                    db,
                    tenant.id,
                    organization.id,
                    admin_email="owner@example.com",
                    admin_name="恢复管理员",
                    admin_password="RecoveryPass123",
                )
                await seed.refresh_quota_usage_from_authoritative_rows(db, tenant_id)
                await db.commit()

        async with owner_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)) == 1
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 1

        monkeypatch.setattr(seed, "refresh_quota_usage_from_authoritative_rows", real_refresh)
        async with runtime_factory() as db:
            await seed.lock_quota_rollout_state(db)
            tenant = await seed._open_tenant_scope(db, tenant_id)
            organization = await seed._get_default_org(db, tenant_id)
            await seed._ensure_demo_accounts(
                db,
                tenant.id,
                organization.id,
                admin_email="owner@example.com",
                admin_name="恢复管理员",
                admin_password="RecoveryPass123",
            )
            await seed.refresh_quota_usage_from_authoritative_rows(db, tenant_id)
            await db.commit()

        async with owner_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)) == 5
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 5
            assert await db.get(Account, admin_id) is not None
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_demo_scan_history_and_quota_refresh_share_one_retryable_transaction(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == "demo"))
            assert tenant_id is not None
            activated_public_ids = list(
                (
                    await db.execute(
                        text(
                            "SELECT public_id FROM code_items WHERE tenant_id=:tenant_id "
                            "AND status='activated' ORDER BY public_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                ).scalars()
            )
            assert len(activated_public_ids) >= 4
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
                )
                == 28
            )
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 5 and quota.scans == 28
            await db.execute(text("DELETE FROM scan_events WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id})
            quota.scans = 0

        real_refresh = seed.refresh_quota_usage_from_authoritative_rows

        async def fail_refresh(*_args, **_kwargs):
            raise RuntimeError("scan quota refresh unavailable")

        monkeypatch.setattr(seed, "control_session", owner_factory)
        monkeypatch.setattr(seed, "refresh_quota_usage_from_authoritative_rows", fail_refresh)
        with pytest.raises(RuntimeError, match="scan quota refresh unavailable"):
            await seed._ensure_demo_scan_history_and_quota(tenant_id, activated_public_ids)

        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
                )
                == 0
            )
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 5 and quota.scans == 0

        monkeypatch.setattr(seed, "refresh_quota_usage_from_authoritative_rows", real_refresh)
        await seed._ensure_demo_scan_history_and_quota(tenant_id, activated_public_ids)

        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
                )
                == 28
            )
            quota = await db.get(TenantQuotaUsage, tenant_id)
            assert quota is not None and quota.accounts == 5 and quota.scans == 28
    finally:
        await owner_engine.dispose()


async def test_seed_all_reconciles_identity_atomically_and_idempotently(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            await _assert_cli_lifecycle_actor_and_cleanup(
                db,
                tenant_slug="demo",
                admin_email="admin@demo.com",
            )
            account = await db.scalar(
                select(Account).options(selectinload(Account.roles)).where(Account.email == "ops@demo.com")
            )
            assert account is not None
            tenant_id = account.tenant_id
            expected_organization_id = account.organization_id
            initial_version = account.auth_version
            viewer = await db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == "viewer"))
            assert viewer is not None
            wrong_org = Organization(tenant_id=tenant_id, name="错误组织")
            db.add(wrong_org)
            await db.flush()
            account.organization_id = wrong_org.id
            account.hashed_password = hash_password("WrongPassword1")
            account.name = "错误运营姓名"
            await db.execute(
                account_roles.delete().where(
                    account_roles.c.tenant_id == tenant_id,
                    account_roles.c.account_id == account.id,
                )
            )
            await db.execute(
                account_roles.insert().values(tenant_id=tenant_id, account_id=account.id, role_id=viewer.id)
            )
            session_id = await _create_active_session(db, account)

        await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).options(selectinload(Account.roles)).where(Account.email == "ops@demo.com")
            )
            assert account is not None
            assert account.organization_id == expected_organization_id
            assert account.name == "活动运营"
            assert verify_password("Ops123456", account.hashed_password)
            assert [role.name for role in account.roles] == ["operator"]
            assert account.auth_version == initial_version + 1
            revoked_at = await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == session_id))
            assert revoked_at is not None
            audit_count = await db.scalar(
                select(func.count())
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.action == "account_identity_reconciled",
                    PlatformAuditLog.resource == f"account:{account.id}",
                )
            )
            assert audit_count == 1
            audit = await db.scalar(
                select(PlatformAuditLog).where(
                    PlatformAuditLog.action == "account_identity_reconciled",
                    PlatformAuditLog.resource == f"account:{account.id}",
                )
            )
            assert audit is not None and "name" in audit.details["changed_fields"]
            stable_version = account.auth_version

        await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
        async with owner_factory() as db:
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None and account.auth_version == stable_version
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(PlatformAuditLog)
                    .where(
                        PlatformAuditLog.action == "account_identity_reconciled",
                        PlatformAuditLog.resource == f"account:{account.id}",
                    )
                )
                == 1
            )

        async with owner_factory() as db, db.begin():
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None
            account.hashed_password = hash_password("RollbackPassword1")
            rollback_version = account.auth_version
            rollback_session = await _create_active_session(db, account)

        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(seed, "write_audit_log", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            async with runtime_factory() as db, db.begin():
                await seed.set_session_tenant_context(db, tenant_id)
                await lock_quota_rollout_state(db)
                await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
                await seed._ensure_demo_accounts(db, tenant_id, expected_organization_id)

        async with owner_factory() as db:
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None
            assert verify_password("RollbackPassword1", account.hashed_password)
            assert account.auth_version == rollback_version
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == rollback_session)) is None
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_baseline_reconciliation_revokes_session_once(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(_run_cli, migrated_pg_url, "baseline", "build", "--target", BASELINE_TENANT_SLUG)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            tenant = await db.scalar(select(Tenant).where(Tenant.slug == BASELINE_TENANT_SLUG))
            assert tenant is not None
            await _assert_cli_lifecycle_actor_and_cleanup(
                db,
                tenant_slug=BASELINE_TENANT_SLUG,
                admin_email=BASELINE_ADMIN_EMAIL,
            )
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None
            initial_version = account.auth_version
            expected_organization_id = account.organization_id
            wrong_org = Organization(tenant_id=tenant.id, name="错误基准组织")
            db.add(wrong_org)
            await db.flush()
            account.organization_id = wrong_org.id
            account.hashed_password = hash_password("WrongBaseline1")
            account.name = "错误基准姓名"
            session_id = await _create_active_session(db, account)

        await asyncio.to_thread(_run_cli, migrated_pg_url, "baseline", "build", "--target", BASELINE_TENANT_SLUG)
        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None
            assert account.organization_id == expected_organization_id
            assert account.name == "基准品牌管理员"
            assert account.auth_version == initial_version + 1
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == session_id)) is not None
            audit = await db.scalar(
                select(PlatformAuditLog).where(
                    PlatformAuditLog.action == "account_identity_reconciled",
                    PlatformAuditLog.resource == f"account:{account.id}",
                    PlatformAuditLog.operator_id == "baseline-cli",
                )
            )
            assert audit is not None
            assert audit.details["before"]["organization"]["name"] == "错误基准组织"
            assert audit.details["after"]["roles"][0]["name"] == "admin"
            stable_version = account.auth_version

        await asyncio.to_thread(_run_cli, migrated_pg_url, "baseline", "build", "--target", BASELINE_TENANT_SLUG)
        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None and account.auth_version == stable_version
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(PlatformAuditLog)
                    .where(
                        PlatformAuditLog.action == "account_identity_reconciled",
                        PlatformAuditLog.resource == f"account:{account.id}",
                        PlatformAuditLog.operator_id == "baseline-cli",
                    )
                )
                == 1
            )

        async with owner_factory() as db, db.begin():
            tenant = await db.scalar(select(Tenant).where(Tenant.slug == BASELINE_TENANT_SLUG))
            assert tenant is not None
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None
            account.hashed_password = hash_password("RollbackBaseline1")
            rollback_version = account.auth_version
            rollback_session = await _create_active_session(db, account)

        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        with monkeypatch.context() as audit_failure:
            audit_failure.setattr(baseline_cli, "write_audit_log", fail_audit)
            with pytest.raises(RuntimeError, match="audit unavailable"):
                async with runtime_factory() as db, db.begin():
                    await seed.set_session_tenant_context(db, tenant.id)
                    await lock_quota_rollout_state(db)
                    scoped_tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant.id).with_for_update())
                    assert scoped_tenant is not None
                    await baseline_cli._ensure_runtime_admin(
                        db,
                        scoped_tenant,
                        baseline_cli._TenantSeedRef(
                            tenant_id=tenant.id,
                            tenant_slug=BASELINE_TENANT_SLUG,
                            admin_email=BASELINE_ADMIN_EMAIL,
                            admin_name="基准品牌管理员",
                            admin_password=baseline_cli.BASELINE_ADMIN_PASSWORD,
                        ),
                    )

        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None
            assert verify_password("RollbackBaseline1", account.hashed_password)
            assert account.auth_version == rollback_version
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == rollback_session)) is None

        # The shared acceptance helper must preserve the production split-role
        # boundary when a previous same-database test leaves identity drift.
        await seed_baseline(migrated_pg_url)
        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).where(Account.tenant_id == tenant.id, Account.email == BASELINE_ADMIN_EMAIL)
            )
            assert account is not None
            assert verify_password(baseline_cli.BASELINE_ADMIN_PASSWORD, account.hashed_password)
            assert account.auth_version == rollback_version + 1
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == rollback_session)) is not None
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_rich_seed_reconciles_identity_session_and_audit_atomically(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db, db.begin():
            account = await db.scalar(
                select(Account).options(selectinload(Account.roles)).where(Account.email == "ops@demo.com")
            )
            assert account is not None
            tenant_id = account.tenant_id
            expected_organization_id = account.organization_id
            initial_version = account.auth_version
            viewer = await db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == "viewer"))
            assert viewer is not None
            wrong_org = Organization(tenant_id=tenant_id, name="错误富演示组织")
            db.add(wrong_org)
            await db.flush()
            account.organization_id = wrong_org.id
            account.hashed_password = hash_password("WrongRichSeed1")
            account.name = "错误富演示姓名"
            await db.execute(
                account_roles.delete().where(
                    account_roles.c.tenant_id == tenant_id,
                    account_roles.c.account_id == account.id,
                )
            )
            await db.execute(
                account_roles.insert().values(tenant_id=tenant_id, account_id=account.id, role_id=viewer.id)
            )
            session_id = await _create_active_session(db, account)

        async with runtime_factory() as db, db.begin():
            await seed.set_session_tenant_context(db, tenant_id)
            await rich_seed._ensure_accounts(db, tenant_id, expected_organization_id)

        async with owner_factory() as db:
            account = await db.scalar(
                select(Account).options(selectinload(Account.roles)).where(Account.email == "ops@demo.com")
            )
            assert account is not None
            assert account.organization_id == expected_organization_id
            assert account.name == "活动运营"
            assert verify_password("Ops123456", account.hashed_password)
            assert [role.name for role in account.roles] == ["operator"]
            assert account.auth_version == initial_version + 1
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == session_id)) is not None
            audit = await db.scalar(
                select(PlatformAuditLog).where(
                    PlatformAuditLog.action == "account_identity_reconciled",
                    PlatformAuditLog.resource == f"account:{account.id}",
                    PlatformAuditLog.operator_id == "seed-demo",
                )
            )
            assert audit is not None
            assert audit.details["before"]["organization"]["name"] == "错误富演示组织"
            assert audit.details["after"]["roles"][0]["name"] == "operator"
            stable_version = account.auth_version
            stable_hash = account.hashed_password

        async with runtime_factory() as db, db.begin():
            await seed.set_session_tenant_context(db, tenant_id)
            await rich_seed._ensure_accounts(db, tenant_id, expected_organization_id)
        async with owner_factory() as db:
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None
            assert account.auth_version == stable_version
            assert account.hashed_password == stable_hash
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(PlatformAuditLog)
                    .where(
                        PlatformAuditLog.action == "account_identity_reconciled",
                        PlatformAuditLog.resource == f"account:{account.id}",
                        PlatformAuditLog.operator_id == "seed-demo",
                    )
                )
                == 1
            )

        async with owner_factory() as db, db.begin():
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None
            account.hashed_password = hash_password("RollbackRichSeed1")
            rollback_version = account.auth_version
            rollback_session = await _create_active_session(db, account)

        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(rich_seed, "write_audit_log", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            async with runtime_factory() as db, db.begin():
                await seed.set_session_tenant_context(db, tenant_id)
                await rich_seed._ensure_accounts(db, tenant_id, expected_organization_id)

        async with owner_factory() as db:
            account = await db.scalar(select(Account).where(Account.email == "ops@demo.com"))
            assert account is not None
            assert verify_password("RollbackRichSeed1", account.hashed_password)
            assert account.auth_version == rollback_version
            assert await db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == rollback_session)) is None
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_production_seed_guard_makes_zero_writes(migrated_pg_url: str) -> None:
    guarded_slug = f"production-guard-{uuid.uuid4().hex[:8]}"
    await asyncio.to_thread(
        _run_cli,
        migrated_pg_url,
        "all",
        "--slug",
        guarded_slug,
        "--allow-non-demo-target",
        production=True,
        succeeds=False,
    )
    owner_engine = create_async_engine(migrated_pg_url)
    try:
        async with owner_engine.connect() as conn:
            assert await conn.scalar(select(func.count()).select_from(Tenant).where(Tenant.slug == guarded_slug)) == 0
            before_count = await conn.scalar(select(func.count()).select_from(Tenant))
        await asyncio.to_thread(
            _run_seed_demo_script,
            migrated_pg_url,
            "generate",
            "--target",
            "demo",
            production=True,
            succeeds=False,
        )
        await asyncio.to_thread(
            _run_cli,
            migrated_pg_url,
            "baseline",
            "build",
            "--target",
            BASELINE_TENANT_SLUG,
            production=True,
            succeeds=False,
        )
        async with owner_engine.connect() as conn:
            assert await conn.scalar(select(func.count()).select_from(Tenant)) == before_count
    finally:
        await owner_engine.dispose()
