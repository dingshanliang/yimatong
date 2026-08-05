"""真实 PostgreSQL 上的代运营授权并发幂等证明。"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.models.tenant import Account, AgencyAuthorization, Organization, Tenant, TenantType
from app.services import agency_auth as agency_auth_service


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_concurrent_duplicate_agency_authorization_reuses_active_result(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    client_tenant_id = uuid.uuid4()
    agency_tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()

    async with factory() as db, db.begin():
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        db.add_all(
            [
                Tenant(
                    id=client_tenant_id,
                    name="并发授权品牌",
                    slug=f"agency-client-{client_tenant_id.hex[:8]}",
                    tenant_type=TenantType.brand,
                ),
                Tenant(
                    id=agency_tenant_id,
                    name="并发授权服务商",
                    slug=f"agency-provider-{agency_tenant_id.hex[:8]}",
                    tenant_type=TenantType.agency,
                ),
            ]
        )
        await db.flush()
        organization = Organization(tenant_id=client_tenant_id, name="品牌组织")
        db.add(organization)
        await db.flush()
        db.add(
            Account(
                id=account_id,
                tenant_id=client_tenant_id,
                organization_id=organization.id,
                email=f"concurrent-{account_id.hex[:8]}@example.com",
                hashed_password="not-used",
                name="并发授权管理员",
            )
        )

    monkeypatch.setattr(agency_auth_service, "_is_pg", True)
    monkeypatch.setattr(database, "control_session_factory", factory)
    first_inserted = asyncio.Event()

    async def authorize(scope: list[str], hold_lock: bool = False) -> uuid.UUID:
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            authorization = await agency_auth_service.authorize_agency(
                db,
                agency_tenant_id=agency_tenant_id,
                client_tenant_id=client_tenant_id,
                scope=scope,
                granted_by=account_id,
            )
            if hold_lock:
                first_inserted.set()
                await asyncio.sleep(0.1)
            return authorization.id

    first_task = asyncio.create_task(authorize(["pages"], hold_lock=True))
    await first_inserted.wait()
    second_task = asyncio.create_task(authorize(["pages", "campaigns"]))

    try:
        first_id, second_id = await asyncio.gather(first_task, second_task)
        assert first_id == second_id
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(AgencyAuthorization)
                    .where(
                        AgencyAuthorization.agency_tenant_id == agency_tenant_id,
                        AgencyAuthorization.client_tenant_id == client_tenant_id,
                        AgencyAuthorization.status == "active",
                    )
                )
            ).scalar_one()
            authorization = (
                await db.execute(select(AgencyAuthorization).where(AgencyAuthorization.id == first_id))
            ).scalar_one()
            assert count == 1
            assert authorization.scope == ["pages", "campaigns"]
    finally:
        await engine.dispose()
