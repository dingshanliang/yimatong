from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.audit import PlatformAuditLog
from app.models.plan import TenantQuotaUsage
from app.models.tenant import Account, Role, Tenant, TenantType, account_roles
from scripts.seed_demo import DEMO_AGENCY_EMAIL, _ensure_demo_agency_identity

pytestmark = pytest.mark.acceptance


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
