from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.core import database
from app.core.context import reset_request_tenant_id, set_request_tenant_id
from app.models.audit import PlatformAuditLog
from app.models.auth_security import AuthSession
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    Organization,
    Role,
    Tenant,
    TenantType,
    account_roles,
)
from app.services.quota import lock_quota_rollout_state
from app.utils.security import hash_password

pytestmark = pytest.mark.acceptance


def _runtime_url(owner_url: str) -> str:
    return owner_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _request(
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    path: str = "/api/v1/organizations",
) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("acceptance", 80),
            "client": ("127.0.0.1", 1),
        }
    )
    request.state.tenant_id = str(tenant_id)
    request.state.original_tenant_id = None
    request.state.auth_method = "jwt"
    request.state.account_id = str(account_id)
    request.state.auth_version = 0
    request.state.role = "admin"
    request.state.session_id = None
    return request


async def _seed_admin(owner_factory: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with owner_factory() as db, db.begin():
        tenant = Tenant(name="mutation race", slug=f"mutation-race-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        organization = Organization(tenant_id=tenant.id, name="总部")
        role = Role(tenant_id=tenant.id, name="admin")
        operator_role = Role(tenant_id=tenant.id, name="operator")
        db.add_all([organization, role, operator_role])
        await db.flush()
        account = Account(
            tenant_id=tenant.id,
            organization_id=organization.id,
            email=f"admin-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="管理员",
        )
        db.add(account)
        await db.flush()
        await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=role.id))
        recovery_admin = Account(
            tenant_id=tenant.id,
            organization_id=organization.id,
            email=f"recovery-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="恢复管理员",
        )
        db.add(recovery_admin)
        await db.flush()
        await db.execute(
            account_roles.insert().values(
                tenant_id=tenant.id,
                account_id=recovery_admin.id,
                role_id=role.id,
            )
        )
        return tenant.id, account.id


async def _write_business_and_audit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    marker: str,
) -> None:
    organization = Organization(tenant_id=tenant_id, name=marker)
    db.add(organization)
    await db.flush()
    db.add(
        PlatformAuditLog(
            operator_id=str(account_id),
            target_tenant_id=str(tenant_id),
            action="mutation_linearization_probe",
            resource=f"organization:{organization.id}",
            details={"marker": marker},
        )
    )
    await db.flush()


async def _assert_write_counts(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    marker: str,
    expected: int,
) -> None:
    async with owner_factory() as db:
        business_writes = await db.scalar(
            select(text("count(*)"))
            .select_from(Organization)
            .where(Organization.tenant_id == tenant_id, Organization.name == marker)
        )
        audit_writes = await db.scalar(
            select(text("count(*)"))
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.target_tenant_id == str(tenant_id),
                PlatformAuditLog.action == "mutation_linearization_probe",
                PlatformAuditLog.details["marker"].as_string() == marker,
            )
        )
        assert business_writes == expected
        assert audit_writes == expected


async def _revoke_account(
    runtime_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    async with runtime_factory() as db, db.begin():
        await database.set_session_tenant_context(db, tenant_id)
        await lock_quota_rollout_state(db)
        await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        await db.execute(
            text("UPDATE accounts SET is_active=false, auth_version=auth_version+1 WHERE id=:account_id"),
            {"account_id": account_id},
        )
        if locked is not None:
            locked.set()
        if release is not None:
            await release.wait()


@pytest.mark.parametrize("revocation_first", [True, False])
async def test_account_revocation_and_stale_mutation_have_one_serial_order(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    revocation_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id, account_id = await _seed_admin(owner_factory)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(tenant_id, account_id)

    async def stale_write() -> bool:
        context_token = set_request_tenant_id(str(tenant_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            await _write_business_and_audit(db, tenant_id, account_id, "stale write")
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
            return True
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if revocation_first:
            locked = asyncio.Event()
            release = asyncio.Event()
            revoke_task = asyncio.create_task(
                _revoke_account(runtime_factory, tenant_id, account_id, locked=locked, release=release)
            )
            await locked.wait()
            stale_task = asyncio.create_task(stale_write())
            await asyncio.sleep(0.1)
            assert not stale_task.done()
            release.set()
            await revoke_task
            with pytest.raises(HTTPException) as exc_info:
                await stale_task
            assert exc_info.value.status_code == 401
            expected_writes = 0
        else:
            context_token = set_request_tenant_id(str(tenant_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                await _write_business_and_audit(db, tenant_id, account_id, "stale write")
                revoke_task = asyncio.create_task(_revoke_account(runtime_factory, tenant_id, account_id))
                await asyncio.sleep(0.1)
                assert not revoke_task.done()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await revoke_task
            finally:
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_writes = 1

        await _assert_write_counts(owner_factory, tenant_id, "stale write", expected_writes)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_committed_durable_session_revocation_blocks_request_write_and_audit(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id, account_id = await _seed_admin(owner_factory)
    session_id = uuid.uuid4()
    async with owner_factory() as db, db.begin():
        db.add(
            AuthSession(
                id=session_id,
                account_id=account_id,
                tenant_id=tenant_id,
                auth_version=0,
                current_refresh_jti=str(uuid.uuid4()),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                revoked_at=datetime.now(UTC),
            )
        )
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(tenant_id, account_id)
    request.state.session_id = str(session_id)
    context_token = set_request_tenant_id(str(tenant_id))
    dependency = database.get_db(request)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await anext(dependency)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "登录会话已撤销或过期"
        await _assert_write_counts(owner_factory, tenant_id, "revoked session write", 0)
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def _demote_account_role(
    runtime_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    async with runtime_factory() as db, db.begin():
        await database.set_session_tenant_context(db, tenant_id)
        await lock_quota_rollout_state(db)
        await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        operator_role_id = await db.scalar(select(Role.id).where(Role.tenant_id == tenant_id, Role.name == "operator"))
        await db.execute(
            account_roles.delete().where(
                account_roles.c.tenant_id == tenant_id,
                account_roles.c.account_id == account_id,
            )
        )
        await db.execute(
            account_roles.insert().values(
                tenant_id=tenant_id,
                account_id=account_id,
                role_id=operator_role_id,
            )
        )
        if locked is not None:
            locked.set()
        if release is not None:
            await release.wait()


@pytest.mark.parametrize("demotion_first", [True, False])
async def test_current_role_demotion_and_stale_admin_request_are_serialized(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    demotion_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id, account_id = await _seed_admin(owner_factory)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(tenant_id, account_id)

    async def stale_write() -> None:
        context_token = set_request_tenant_id(str(tenant_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            await _write_business_and_audit(db, tenant_id, account_id, "demoted role write")
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if demotion_first:
            locked = asyncio.Event()
            release = asyncio.Event()
            demotion_task = asyncio.create_task(
                _demote_account_role(runtime_factory, tenant_id, account_id, locked, release)
            )
            await locked.wait()
            stale_task = asyncio.create_task(stale_write())
            await asyncio.sleep(0.1)
            assert not stale_task.done()
            release.set()
            await demotion_task
            with pytest.raises(HTTPException) as exc_info:
                await stale_task
            assert exc_info.value.status_code == 401
            expected_writes = 0
        else:
            context_token = set_request_tenant_id(str(tenant_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                await _write_business_and_audit(db, tenant_id, account_id, "demoted role write")
                demotion_task = asyncio.create_task(_demote_account_role(runtime_factory, tenant_id, account_id))
                await asyncio.sleep(0.1)
                assert not demotion_task.done()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await demotion_task
            finally:
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_writes = 1
        await _assert_write_counts(owner_factory, tenant_id, "demoted role write", expected_writes)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def _seed_acting_admin(
    owner_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    async with owner_factory() as db, db.begin():
        agency = Tenant(
            name="mutation agency",
            slug=f"mutation-agency-{uuid.uuid4().hex[:8]}",
            tenant_type=TenantType.agency,
        )
        client = Tenant(name="mutation client", slug=f"mutation-client-{uuid.uuid4().hex[:8]}")
        db.add_all([agency, client])
        await db.flush()
        agency_org = Organization(tenant_id=agency.id, name="代运营总部")
        client_org = Organization(tenant_id=client.id, name="客户总部")
        agency_admin_role = Role(tenant_id=agency.id, name="admin")
        client_admin_role = Role(tenant_id=client.id, name="admin")
        db.add_all([agency_org, client_org, agency_admin_role, client_admin_role])
        await db.flush()
        agency_admin = Account(
            tenant_id=agency.id,
            organization_id=agency_org.id,
            email=f"agency-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="代运营管理员",
        )
        client_admin = Account(
            tenant_id=client.id,
            organization_id=client_org.id,
            email=f"client-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="客户管理员",
        )
        db.add_all([agency_admin, client_admin])
        await db.flush()
        await db.execute(
            account_roles.insert().values(
                tenant_id=agency.id,
                account_id=agency_admin.id,
                role_id=agency_admin_role.id,
            )
        )
        await db.execute(
            account_roles.insert().values(
                tenant_id=client.id,
                account_id=client_admin.id,
                role_id=client_admin_role.id,
            )
        )
        authorization = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=client.id,
            scope=["products"],
            status=AgencyAuthStatus.active,
            granted_by=client_admin.id,
        )
        db.add(authorization)
        await db.flush()
        return agency.id, client.id, agency_admin.id, authorization.id


async def _change_acting_authorization(
    owner_factory: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    client_id: uuid.UUID,
    authorization_id: uuid.UUID,
    change: str,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    request = _request(client_id, uuid.uuid4(), path="/api/v1/products")
    request.state.original_tenant_id = str(agency_id)
    async with owner_factory() as db, db.begin():
        await lock_quota_rollout_state(db)
        await database._lock_request_tenants(db, request, client_id)
        authorization = await db.get(AgencyAuthorization, authorization_id)
        assert authorization is not None
        if change == "revoked":
            authorization.status = AgencyAuthStatus.revoked
            authorization.revoked_at = datetime.now(UTC)
        else:
            authorization.scope = ["analytics"]
        await db.flush()
        if locked is not None:
            locked.set()
        if release is not None:
            await release.wait()


@pytest.mark.parametrize("change", ["revoked", "narrowed"])
@pytest.mark.parametrize("authorization_change_first", [True, False])
async def test_acting_authorization_change_and_request_are_serialized_after_ordered_tenant_locks(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    authorization_change_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    agency_id, client_id, account_id, authorization_id = await _seed_acting_admin(owner_factory)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(client_id, account_id, path="/api/v1/products")
    request.state.original_tenant_id = str(agency_id)
    request.state.acting_tenant_id = str(client_id)
    marker = f"acting {change} write"

    async def stale_write() -> None:
        context_token = set_request_tenant_id(str(client_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            await _write_business_and_audit(db, client_id, account_id, marker)
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if authorization_change_first:
            locked = asyncio.Event()
            release = asyncio.Event()
            change_task = asyncio.create_task(
                _change_acting_authorization(
                    owner_factory,
                    agency_id,
                    client_id,
                    authorization_id,
                    change,
                    locked,
                    release,
                )
            )
            await locked.wait()
            stale_task = asyncio.create_task(stale_write())
            await asyncio.sleep(0.1)
            assert not stale_task.done()
            release.set()
            await change_task
            with pytest.raises(HTTPException) as exc_info:
                await stale_task
            assert exc_info.value.status_code == 403
            expected_writes = 0
        else:
            context_token = set_request_tenant_id(str(client_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                await _write_business_and_audit(db, client_id, account_id, marker)
                change_task = asyncio.create_task(
                    _change_acting_authorization(
                        owner_factory,
                        agency_id,
                        client_id,
                        authorization_id,
                        change,
                    )
                )
                await asyncio.sleep(0.1)
                assert not change_task.done()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await change_task
            finally:
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_writes = 1
        await _assert_write_counts(owner_factory, client_id, marker, expected_writes)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_opposite_acting_contexts_lock_tenant_set_without_deadlock(
    migrated_pg_url: str,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    first_tenant, _ = await _seed_admin(owner_factory)
    second_tenant, _ = await _seed_admin(owner_factory)
    barrier = asyncio.Barrier(2)

    async def lock_pair(target: uuid.UUID, original: uuid.UUID) -> None:
        request = _request(target, uuid.uuid4())
        request.state.original_tenant_id = str(original)
        async with runtime_factory() as db, db.begin():
            await database.set_session_tenant_context(db, target)
            await lock_quota_rollout_state(db)
            await barrier.wait()
            await database._lock_request_tenants(db, request, target)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                lock_pair(first_tenant, second_tenant),
                lock_pair(second_tenant, first_tenant),
            ),
            timeout=5,
        )
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
