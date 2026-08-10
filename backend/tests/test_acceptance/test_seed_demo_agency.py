from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.lifecycle_auth import cli_lifecycle_auth_context
from app.models.audit import PlatformAuditLog
from app.models.auth_security import AuthSession
from app.models.plan import TenantQuotaUsage
from app.models.tenant import Account, AgencyAuthorization, Role, Tenant, TenantType, account_roles
from scripts.seed_demo import (
    DEMO_AGENCY_EMAIL,
    DEMO_AGENCY_SCOPES,
    _ensure_demo_agency_authorization,
    _ensure_demo_agency_identity,
)

pytestmark = pytest.mark.acceptance
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_official_demo_seed(owner_url: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "database_url": owner_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"),
            "control_database_url": owner_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@"),
            "migration_database_url": owner_url,
            "environment": "test",
        }
    )
    result = subprocess.run(
        ["uv", "run", "python", "scripts/seed_demo.py", "generate", "--target", "demo"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("identity_residual", [False, True])
async def test_demo_agency_birth_commits_with_an_active_admin(
    migrated_pg_url: str,
    identity_residual: bool,
) -> None:
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    control_engine = create_async_engine(control_url)
    owner_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    agency_slug = f"acceptance-demo-agency-{str(identity_residual).lower()}-{uuid.uuid4().hex[:8]}"
    residual_id: uuid.UUID | None = None
    try:
        if identity_residual:
            async with control_factory() as db, db.begin():
                await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
                await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
                residual = Tenant(
                    name="残留代运营租户",
                    slug=agency_slug,
                    tenant_type=TenantType.agency,
                    plan="pro",
                )
                db.add(residual)
                await db.flush()
                residual_id = residual.id

        async with control_factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            agency_id = await _ensure_demo_agency_identity(db, agency_slug=agency_slug)

        async with owner_factory() as db:
            tenant = await db.get(Tenant, agency_id)
            account = await db.scalar(
                select(Account).where(Account.tenant_id == agency_id, Account.email == DEMO_AGENCY_EMAIL)
            )
            assert tenant is not None and tenant.tenant_type == TenantType.agency
            assert account is not None and account.is_active is True
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(account_roles)
                    .join(
                        Role,
                        (Role.tenant_id == account_roles.c.tenant_id) & (Role.id == account_roles.c.role_id),
                    )
                    .where(
                        account_roles.c.tenant_id == agency_id,
                        account_roles.c.account_id == account.id,
                        Role.name == "admin",
                    )
                )
                == 1
            )
            assert await db.get(TenantQuotaUsage, agency_id) is not None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(PlatformAuditLog)
                    .where(
                        PlatformAuditLog.target_tenant_id == str(agency_id),
                        PlatformAuditLog.action == "brand_tenant_initialized",
                    )
                )
                == 1
            )
            if residual_id is not None:
                assert await db.get(Tenant, residual_id) is None
    finally:
        await control_engine.dispose()
        await owner_engine.dispose()


async def test_demo_agency_authorization_uses_control_path_and_runtime_stays_read_only(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    control_engine = create_async_engine(control_url)
    owner_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    agency_id = uuid.uuid4()
    client_id = uuid.uuid4()
    try:
        async with owner_factory() as db, db.begin():
            db.add_all(
                (
                    Tenant(
                        id=agency_id,
                        name="Seed control agency",
                        slug=f"seed-agency-{agency_id.hex[:12]}",
                        tenant_type=TenantType.agency,
                        plan="pro",
                    ),
                    Tenant(
                        id=client_id,
                        name="Seed control client",
                        slug=f"seed-client-{client_id.hex[:12]}",
                        tenant_type=TenantType.brand,
                        plan="free",
                    ),
                )
            )

        async with control_factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            first_id = await _ensure_demo_agency_authorization(
                db,
                agency_tenant_id=agency_id,
                client_tenant_id=client_id,
            )

        async with owner_factory() as db:
            created_at = await db.scalar(
                select(AgencyAuthorization.created_at).where(AgencyAuthorization.id == first_id)
            )

        async with control_factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            second_id = await _ensure_demo_agency_authorization(
                db,
                agency_tenant_id=agency_id,
                client_tenant_id=client_id,
            )

        async with owner_factory() as db:
            authorization = await db.get(AgencyAuthorization, first_id)
            assert first_id == second_id
            assert authorization is not None
            assert authorization.scope == DEMO_AGENCY_SCOPES
            assert authorization.created_at == created_at
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgencyAuthorization)
                    .where(
                        AgencyAuthorization.agency_tenant_id == agency_id,
                        AgencyAuthorization.client_tenant_id == client_id,
                    )
                )
                == 1
            )

        savepoint = runtime_pg_conn.transaction()
        await savepoint.start()
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_pg_conn.execute(
                "INSERT INTO agency_authorizations "
                "(id,agency_tenant_id,client_tenant_id,scope,status,granted_at,created_at,updated_at) "
                "VALUES ($1,$2,$3,$4::json,'active',now(),now(),now())",
                uuid.uuid4(),
                agency_id,
                client_id,
                '["products"]',
            )
        await savepoint.rollback()
    finally:
        await control_engine.dispose()
        await owner_engine.dispose()


async def test_official_demo_seed_is_idempotent_on_the_same_database(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    control_engine = create_async_engine(
        migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    )
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with owner_factory() as db:
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not await db.scalar(
                    text("SELECT has_table_privilege('yimatong_app','public.auth_sessions',:privilege)"),
                    {"privilege": privilege},
                )

        async with control_factory() as db:
            assert await db.scalar(text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
            for privilege in ("SELECT", "INSERT", "DELETE"):
                assert await db.scalar(
                    text("SELECT has_table_privilege(current_user,'public.auth_sessions',:privilege)"),
                    {"privilege": privilege},
                )

        await asyncio.to_thread(_run_official_demo_seed, migrated_pg_url)

        async with owner_factory() as db:
            agency_id = await db.scalar(select(Tenant.id).where(Tenant.slug == "demo-agency"))
            client_id = await db.scalar(select(Tenant.id).where(Tenant.slug == "demo"))
            admin = await db.scalar(
                select(Account).where(Account.tenant_id == client_id, Account.email == "admin@demo.com")
            )
            assert admin is not None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(
                        AuthSession.tenant_id == client_id,
                        AuthSession.current_refresh_jti.like("cli-%"),
                    )
                )
                == 0
            )
            lifecycle_operators = set(
                (
                    await db.scalars(
                        select(PlatformAuditLog.operator_id).where(
                            PlatformAuditLog.target_tenant_id == str(client_id),
                            PlatformAuditLog.action == "code_activate",
                        )
                    )
                ).all()
            )
            assert lifecycle_operators == {str(admin.id)}
            authorization = await db.scalar(
                select(AgencyAuthorization).where(
                    AgencyAuthorization.agency_tenant_id == agency_id,
                    AgencyAuthorization.client_tenant_id == client_id,
                )
            )
            assert authorization is not None
            authorization_id = authorization.id
            created_at = authorization.created_at

        async with control_factory() as db:
            assert (
                await db.scalar(
                    select(Account.id).where(
                        Account.tenant_id == client_id,
                        Account.email == "admin@demo.com",
                    )
                )
                is None
            )
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(client_id)}
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Account)
                    .join(
                        account_roles,
                        (account_roles.c.tenant_id == Account.tenant_id) & (account_roles.c.account_id == Account.id),
                    )
                    .join(
                        Role,
                        (Role.tenant_id == account_roles.c.tenant_id) & (Role.id == account_roles.c.role_id),
                    )
                    .where(
                        Account.tenant_id == client_id,
                        Account.id == admin.id,
                        Account.is_active.is_(True),
                        Role.name == "admin",
                    )
                )
                == 1
            )
            await db.rollback()

        failed_session_id: uuid.UUID | None = None
        with pytest.raises(RuntimeError, match="official seed failure path"):
            async with cli_lifecycle_auth_context(
                control_factory,
                tenant_id=client_id,
                account_id=admin.id,
            ) as session_id:
                failed_session_id = session_id
                raise RuntimeError("official seed failure path")
        assert failed_session_id is not None
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(AuthSession).where(AuthSession.id == failed_session_id)
                )
                == 0
            )

        await asyncio.to_thread(_run_official_demo_seed, migrated_pg_url)

        async with owner_factory() as db:
            authorization = await db.scalar(
                select(AgencyAuthorization).where(
                    AgencyAuthorization.agency_tenant_id == agency_id,
                    AgencyAuthorization.client_tenant_id == client_id,
                )
            )
            assert authorization is not None
            assert authorization.id == authorization_id
            assert authorization.created_at == created_at
            assert authorization.scope == DEMO_AGENCY_SCOPES
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgencyAuthorization)
                    .where(
                        AgencyAuthorization.agency_tenant_id == agency_id,
                        AgencyAuthorization.client_tenant_id == client_id,
                    )
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(
                        AuthSession.tenant_id == client_id,
                        AuthSession.current_refresh_jti.like("cli-%"),
                    )
                )
                == 0
            )
    finally:
        await control_engine.dispose()
        await owner_engine.dispose()
