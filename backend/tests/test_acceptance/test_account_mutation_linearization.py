from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
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
    Permission,
    Role,
    Tenant,
    TenantType,
    account_roles,
    role_permissions,
)
from app.services import auth as auth_service
from app.services.quota import lock_quota_rollout_state
from app.services.tenant import update_tenant
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


async def _seed_admin(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_type: TenantType = TenantType.brand,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with owner_factory() as db, db.begin():
        tenant = Tenant(
            name="mutation race",
            slug=f"mutation-race-{uuid.uuid4().hex[:8]}",
            tenant_type=tenant_type,
        )
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
            await asyncio.wait_for(locked.wait(), timeout=5)
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
            await asyncio.wait_for(locked.wait(), timeout=5)
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


async def _seed_live_acting_session(
    owner_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, dict, uuid.UUID]:
    agency_id, client_id, account_id, authorization_id = await _seed_acting_admin(owner_factory)
    session_id = uuid.uuid4()
    async with owner_factory() as db, db.begin():
        admin_role = await db.scalar(select(Role).where(Role.tenant_id == agency_id, Role.name == "admin"))
        assert admin_role is not None
        operator_role = Role(tenant_id=agency_id, name="operator")
        permission = Permission(tenant_id=agency_id, code="product:read")
        db.add_all([operator_role, permission])
        await db.flush()
        agency_org = await db.scalar(select(Organization).where(Organization.tenant_id == agency_id))
        assert agency_org is not None
        recovery_admin = Account(
            tenant_id=agency_id,
            organization_id=agency_org.id,
            email=f"acting-recovery-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="代运营恢复管理员",
        )
        db.add(recovery_admin)
        await db.flush()
        await db.execute(
            account_roles.insert().values(
                tenant_id=agency_id,
                account_id=recovery_admin.id,
                role_id=admin_role.id,
            )
        )
        await db.execute(
            role_permissions.insert().values(
                tenant_id=agency_id,
                role_id=admin_role.id,
                permission_id=permission.id,
            )
        )
        account = await db.scalar(select(Account).options(selectinload(Account.roles)).where(Account.id == account_id))
        assert account is not None
        token_pair = auth_service._build_token_pair(account, TenantType.agency.value, session_id)
        refresh_payload = auth_service.decode_token(token_pair["refresh_token"])
        db.add(
            AuthSession(
                id=session_id,
                account_id=account_id,
                tenant_id=agency_id,
                auth_version=account.auth_version,
                current_refresh_jti=refresh_payload["jti"],
                expires_at=datetime.fromtimestamp(refresh_payload["exp"], tz=UTC),
            )
        )
        return agency_id, client_id, account_id, authorization_id, token_pair, operator_role.id


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
            # Scope is immutable at head. A real narrowing replaces the live
            # grant, so model that lifecycle instead of mutating scope in place.
            authorization.status = AgencyAuthStatus.revoked
            authorization.revoked_at = datetime.now(UTC)
            await db.flush()
            db.add(
                AgencyAuthorization(
                    agency_tenant_id=agency_id,
                    client_tenant_id=client_id,
                    scope=["analytics"],
                    status=AgencyAuthStatus.active,
                    granted_by=authorization.granted_by,
                )
            )
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
            await asyncio.wait_for(locked.wait(), timeout=5)
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


@pytest.mark.parametrize("authorization_change_first", [True, False])
async def test_acting_read_and_authorization_revocation_have_one_serial_order(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
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
    request.scope["method"] = "GET"
    request.state.original_tenant_id = str(agency_id)
    request.state.acting_tenant_id = str(client_id)
    request.state.tenant_type = TenantType.agency.value

    async def acting_read() -> int:
        context_token = set_request_tenant_id(str(client_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            count = await db.scalar(
                select(text("count(*)")).select_from(Organization).where(Organization.tenant_id == client_id)
            )
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
            return int(count or 0)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if authorization_change_first:
            locked = asyncio.Event()
            release = asyncio.Event()
            revoke_task = asyncio.create_task(
                _change_acting_authorization(
                    owner_factory,
                    agency_id,
                    client_id,
                    authorization_id,
                    "revoked",
                    locked,
                    release,
                )
            )
            await asyncio.wait_for(locked.wait(), timeout=5)
            read_task = asyncio.create_task(acting_read())
            await asyncio.sleep(0.1)
            assert not read_task.done()
            release.set()
            await revoke_task
            with pytest.raises(HTTPException) as exc_info:
                await read_task
            assert exc_info.value.status_code == 403
        else:
            context_token = set_request_tenant_id(str(client_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                count = await db.scalar(
                    select(text("count(*)")).select_from(Organization).where(Organization.tenant_id == client_id)
                )
                revoke_task = asyncio.create_task(
                    _change_acting_authorization(
                        owner_factory,
                        agency_id,
                        client_id,
                        authorization_id,
                        "revoked",
                    )
                )
                await asyncio.sleep(0.1)
                assert not revoke_task.done()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await revoke_task
            finally:
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            assert count == 1
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def _demote_acting_account(
    owner_factory: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    account_id: uuid.UUID,
    operator_role_id: uuid.UUID,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    async with owner_factory() as db, db.begin():
        await lock_quota_rollout_state(db)
        await db.scalar(select(Tenant.id).where(Tenant.id == agency_id).with_for_update())
        await db.execute(
            account_roles.delete().where(
                account_roles.c.tenant_id == agency_id,
                account_roles.c.account_id == account_id,
            )
        )
        await db.execute(
            account_roles.insert().values(
                tenant_id=agency_id,
                account_id=account_id,
                role_id=operator_role_id,
            )
        )
        if locked is not None:
            locked.set()
        if release is not None:
            await release.wait()


@pytest.mark.parametrize("principal_change", ["logout", "replay", "demotion"])
@pytest.mark.parametrize("change_first", [True, False])
async def test_acting_read_revalidates_live_session_and_role_in_one_serial_order(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    principal_change: str,
    change_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    agency_id, client_id, account_id, _, token_pair, operator_role_id = await _seed_live_acting_session(owner_factory)
    refresh_payload = auth_service.decode_token(token_pair["refresh_token"])
    session_id = uuid.UUID(refresh_payload["sid"])
    cache = _RecordingSecurityCache()
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(client_id, account_id, path="/api/v1/products")
    request.scope["method"] = "GET"
    request.state.original_tenant_id = str(agency_id)
    request.state.acting_tenant_id = str(client_id)
    request.state.tenant_type = TenantType.agency.value
    request.state.session_id = str(session_id)
    request.state.permissions = ["product:read"]
    marker = f"acting-read-{principal_change}-{change_first}"
    change_locked = asyncio.Event()
    change_release = asyncio.Event()

    if principal_change == "replay":
        async with owner_factory() as db, db.begin():
            durable_session = await db.get(AuthSession, session_id)
            assert durable_session is not None
            durable_session.current_refresh_jti = uuid.uuid4().hex

        async def verify_without_cache(token: str) -> dict:
            return auth_service.decode_token(token)

        monkeypatch.setattr(auth_service, "verify_refresh_token", verify_without_cache)
        original_reject = auth_service._reject_and_revoke_replayed_family

        async def hold_replay(db, auth_session, security_cache):
            change_locked.set()
            await change_release.wait()
            await original_reject(db, auth_session, security_cache)

        monkeypatch.setattr(auth_service, "_reject_and_revoke_replayed_family", hold_replay)
    elif principal_change == "logout":
        original_lock = database.lock_auth_session_serialization

        async def hold_logout_session_key(db, locked_session_id):
            result = await original_lock(db, locked_session_id)
            task = asyncio.current_task()
            if task is not None and task.get_name() == "logout-holder":
                change_locked.set()
                await change_release.wait()
            return result

        monkeypatch.setattr(database, "lock_auth_session_serialization", hold_logout_session_key)

    async def acting_read() -> int:
        context_token = set_request_tenant_id(str(client_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            count = await db.scalar(
                select(text("count(*)")).select_from(Organization).where(Organization.tenant_id == client_id)
            )
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
            return int(count or 0)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    async def change_principal() -> None:
        if principal_change == "demotion":
            await _demote_acting_account(
                owner_factory,
                agency_id,
                account_id,
                operator_role_id,
                change_locked,
                change_release,
            )
        elif principal_change == "logout":
            async with owner_factory() as db:
                await auth_service.logout_session(db, None, token_pair["refresh_token"], cache)
        else:
            async with owner_factory() as db:
                with pytest.raises(auth_service.AuthError) as exc_info:
                    await auth_service.refresh_access_token(db, token_pair["refresh_token"], cache)
                assert exc_info.value.code == 401

    try:
        if change_first:
            task_name = "logout-holder" if principal_change == "logout" else None
            change_task = asyncio.create_task(change_principal(), name=task_name)
            await asyncio.wait_for(change_locked.wait(), timeout=5)
            read_task = asyncio.create_task(acting_read())
            await asyncio.sleep(0.1)
            assert not read_task.done()
            change_release.set()
            await change_task
            with pytest.raises(HTTPException) as exc_info:
                await read_task
            assert exc_info.value.status_code == 401
            expected_reads = 0
        else:
            context_token = set_request_tenant_id(str(client_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                count = await db.scalar(
                    select(text("count(*)")).select_from(Organization).where(Organization.tenant_id == client_id)
                )
                task_name = "logout-holder" if principal_change == "logout" else None
                change_task = asyncio.create_task(change_principal(), name=task_name)
                await asyncio.sleep(0.1)
                assert not change_locked.is_set()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await asyncio.wait_for(change_locked.wait(), timeout=5)
                change_release.set()
                await change_task
            finally:
                change_release.set()
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_reads = 1
            assert count == 1

        assert expected_reads == (0 if change_first else 1)
        async with owner_factory() as db:
            audit_count = await db.scalar(
                select(text("count(*)"))
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.action == "acting_read_probe",
                    PlatformAuditLog.details["marker"].as_string() == marker,
                )
            )
            assert audit_count == 0
    finally:
        change_release.set()
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


async def _transition_tenant_type(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    next_type: TenantType,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    async with owner_factory() as db, db.begin():
        tenant = await update_tenant(
            db,
            tenant_id,
            tenant_type=next_type.value,
            actor_id="platform-admin",
        )
        assert tenant is not None
        if locked is not None:
            locked.set()
        if release is not None:
            await release.wait()


@pytest.mark.parametrize("transition_first", [True, False])
@pytest.mark.parametrize(
    ("original_type", "next_type"),
    [
        (TenantType.agency, TenantType.brand),
        (TenantType.brand, TenantType.agency),
    ],
)
async def test_tenant_type_transition_and_stale_request_have_one_serial_order(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    transition_first: bool,
    original_type: TenantType,
    next_type: TenantType,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id, account_id = await _seed_admin(owner_factory, original_type)
    business_tenant_id = tenant_id
    request = _request(tenant_id, account_id)
    request.state.tenant_type = original_type.value
    marker = f"type transition {original_type.value} to {next_type.value}"
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def stale_write() -> None:
        context_token = set_request_tenant_id(str(business_tenant_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            await _write_business_and_audit(db, business_tenant_id, account_id, marker)
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if transition_first:
            locked = asyncio.Event()
            release = asyncio.Event()
            transition_task = asyncio.create_task(
                _transition_tenant_type(owner_factory, tenant_id, next_type, locked, release)
            )
            await asyncio.wait_for(locked.wait(), timeout=5)
            stale_task = asyncio.create_task(stale_write())
            await asyncio.sleep(0.1)
            assert not stale_task.done()
            release.set()
            await transition_task
            with pytest.raises(HTTPException) as exc_info:
                await stale_task
            assert exc_info.value.status_code == 401
            expected_writes = 0
        else:
            context_token = set_request_tenant_id(str(business_tenant_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                await _write_business_and_audit(db, business_tenant_id, account_id, marker)
                transition_task = asyncio.create_task(_transition_tenant_type(owner_factory, tenant_id, next_type))
                await asyncio.sleep(0.1)
                assert not transition_task.done()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await transition_task
            finally:
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_writes = 1
        await _assert_write_counts(owner_factory, business_tenant_id, marker, expected_writes)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def _seed_transition_renew_principal(
    owner_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    async with owner_factory() as db, db.begin():
        agency = Tenant(
            name="renew transition agency",
            slug=f"renew-transition-agency-{uuid.uuid4().hex[:8]}",
            tenant_type=TenantType.agency,
        )
        client = Tenant(
            name="renew transition client",
            slug=f"renew-transition-client-{uuid.uuid4().hex[:8]}",
            tenant_type=TenantType.brand,
        )
        db.add_all([agency, client])
        await db.flush()
        organization = Organization(tenant_id=client.id, name="品牌总部")
        role = Role(tenant_id=client.id, name="admin")
        permission = Permission(tenant_id=client.id, code="tenant:manage")
        db.add_all([organization, role, permission])
        await db.flush()
        account = Account(
            tenant_id=client.id,
            organization_id=organization.id,
            email=f"renew-transition-{uuid.uuid4().hex[:8]}@race.test",
            hashed_password=hash_password("Password1"),
            name="品牌管理员",
        )
        db.add(account)
        await db.flush()
        await db.execute(account_roles.insert().values(tenant_id=client.id, account_id=account.id, role_id=role.id))
        await db.execute(
            role_permissions.insert().values(
                tenant_id=client.id,
                role_id=role.id,
                permission_id=permission.id,
            )
        )
        session_id = uuid.uuid4()
        db.add(
            AuthSession(
                id=session_id,
                account_id=account.id,
                tenant_id=client.id,
                auth_version=account.auth_version,
                current_refresh_jti=uuid.uuid4().hex,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        return agency.id, client.id, account.id, session_id


async def _attempt_authorization_renew(
    runtime_factory: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    client_id: uuid.UUID,
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> bool:
    try:
        async with runtime_factory() as db, db.begin():
            await database.set_session_tenant_context(db, client_id)
            await db.scalar(
                text(
                    "SELECT public.renew_agency_authorization("
                    ":authorization_id, :agency_id, :client_id, CAST(:scope AS jsonb), "
                    ":grantor_id, :session_id, NULL)"
                ),
                {
                    "authorization_id": uuid.uuid4(),
                    "agency_id": agency_id,
                    "client_id": client_id,
                    "scope": json.dumps(["products"]),
                    "grantor_id": account_id,
                    "session_id": session_id,
                },
            )
            if locked is not None:
                locked.set()
            if release is not None:
                await release.wait()
        return True
    except DBAPIError:
        return False


async def _attempt_type_transition(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    next_type: TenantType,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> bool:
    from app.services.tenant import TenantTypeTransitionConflict

    try:
        async with owner_factory() as db, db.begin():
            tenant = await update_tenant(
                db,
                tenant_id,
                tenant_type=next_type.value,
                actor_id="platform-admin",
            )
            assert tenant is not None
            if locked is not None:
                locked.set()
            if release is not None:
                await release.wait()
        return True
    except TenantTypeTransitionConflict:
        return False


@pytest.mark.parametrize("target_side", ["agency", "client"])
@pytest.mark.parametrize("transition_first", [True, False])
async def test_tenant_type_transition_and_authorization_renew_have_one_serial_order(
    migrated_pg_url: str,
    target_side: str,
    transition_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    agency_id, client_id, account_id, session_id = await _seed_transition_renew_principal(owner_factory)
    target_id = agency_id if target_side == "agency" else client_id
    original_type = TenantType.agency if target_side == "agency" else TenantType.brand
    next_type = TenantType.brand if target_side == "agency" else TenantType.agency
    first_locked = asyncio.Event()
    release_first = asyncio.Event()

    try:
        if transition_first:
            transition_task = asyncio.create_task(
                _attempt_type_transition(owner_factory, target_id, next_type, first_locked, release_first)
            )
            await asyncio.wait_for(first_locked.wait(), timeout=5)
            renew_task = asyncio.create_task(
                _attempt_authorization_renew(runtime_factory, agency_id, client_id, account_id, session_id)
            )
            await asyncio.sleep(0.1)
            assert not renew_task.done()
            release_first.set()
            assert await transition_task is True
            assert await renew_task is False
        else:
            renew_task = asyncio.create_task(
                _attempt_authorization_renew(
                    runtime_factory,
                    agency_id,
                    client_id,
                    account_id,
                    session_id,
                    first_locked,
                    release_first,
                )
            )
            await asyncio.wait_for(first_locked.wait(), timeout=5)
            transition_task = asyncio.create_task(_attempt_type_transition(owner_factory, target_id, next_type))
            await asyncio.sleep(0.1)
            assert not transition_task.done()
            release_first.set()
            assert await renew_task is True
            assert await transition_task is False

        async with owner_factory() as db:
            target = await db.get(Tenant, target_id)
            account = await db.get(Account, account_id)
            auth_session = await db.get(AuthSession, session_id)
            assert target is not None
            assert account is not None
            assert auth_session is not None
            active_grants = await db.scalar(
                select(text("count(*)"))
                .select_from(AgencyAuthorization)
                .where(
                    AgencyAuthorization.agency_tenant_id == agency_id,
                    AgencyAuthorization.client_tenant_id == client_id,
                    AgencyAuthorization.status == AgencyAuthStatus.active,
                )
            )
            transition_audits = await db.scalar(
                select(text("count(*)"))
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.target_tenant_id == str(target_id),
                    PlatformAuditLog.action == "tenant_type_changed",
                )
            )
            if transition_first:
                assert target.tenant_type == next_type
                assert active_grants == 0
                assert transition_audits == 1
                expected_version = 1 if target_side == "client" else 0
                assert account.auth_version == expected_version
                assert (auth_session.revoked_at is not None) is (target_side == "client")
            else:
                assert target.tenant_type == original_type
                assert active_grants == 1
                assert transition_audits == 0
                assert account.auth_version == 0
                assert auth_session.revoked_at is None
    finally:
        release_first.set()
        await runtime_engine.dispose()
        await owner_engine.dispose()


class _RecordingSecurityCache:
    def __init__(self) -> None:
        self.revoked: set[str] = set()

    async def revoke_token(self, jti: str, ttl: int) -> None:
        assert ttl > 0
        self.revoked.add(jti)


@pytest.mark.parametrize("replay_first", [True, False])
async def test_refresh_replay_revocation_and_business_mutation_share_session_order(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    replay_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(_runtime_url(migrated_pg_url))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id, account_id = await _seed_admin(owner_factory)
    session_id = uuid.uuid4()
    async with owner_factory() as db, db.begin():
        account = await db.get(Account, account_id)
        assert account is not None
        token_pair = auth_service._build_token_pair(account, TenantType.brand.value, session_id)
        refresh_payload = auth_service.decode_token(token_pair["refresh_token"])
        db.add(
            AuthSession(
                id=session_id,
                account_id=account_id,
                tenant_id=tenant_id,
                auth_version=account.auth_version,
                current_refresh_jti=str(uuid.uuid4()),
                expires_at=datetime.fromtimestamp(refresh_payload["exp"], tz=UTC),
            )
        )
    cache = _RecordingSecurityCache()

    async def verify_without_cache(token: str) -> dict:
        return auth_service.decode_token(token)

    monkeypatch.setattr(auth_service, "verify_refresh_token", verify_without_cache)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    request = _request(tenant_id, account_id)
    request.state.session_id = str(session_id)
    request.state.tenant_type = TenantType.brand.value
    marker = f"refresh replay {'first' if replay_first else 'second'}"
    replay_locked = asyncio.Event()
    replay_release = asyncio.Event()
    original_reject = auth_service._reject_and_revoke_replayed_family

    async def hold_replay_lock(db, auth_session, security_cache):
        replay_locked.set()
        await replay_release.wait()
        await original_reject(db, auth_session, security_cache)

    monkeypatch.setattr(auth_service, "_reject_and_revoke_replayed_family", hold_replay_lock)

    async def replay_refresh() -> None:
        async with owner_factory() as db:
            with pytest.raises(auth_service.AuthError) as exc_info:
                await auth_service.refresh_access_token(db, token_pair["refresh_token"], cache)
            assert exc_info.value.code == 401

    async def stale_write() -> None:
        context_token = set_request_tenant_id(str(tenant_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            await _write_business_and_audit(db, tenant_id, account_id, marker)
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    try:
        if replay_first:
            replay_task = asyncio.create_task(replay_refresh())
            await asyncio.wait_for(replay_locked.wait(), timeout=5)
            stale_task = asyncio.create_task(stale_write())
            await asyncio.sleep(0.1)
            assert not stale_task.done()
            replay_release.set()
            await replay_task
            with pytest.raises(HTTPException) as exc_info:
                await stale_task
            assert exc_info.value.status_code == 401
            expected_writes = 0
        else:
            context_token = set_request_tenant_id(str(tenant_id))
            dependency = database.get_db(request)
            try:
                db = await anext(dependency)
                await _write_business_and_audit(db, tenant_id, account_id, marker)
                replay_task = asyncio.create_task(replay_refresh())
                await asyncio.sleep(0.1)
                assert not replay_locked.is_set()
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
                await asyncio.wait_for(replay_locked.wait(), timeout=5)
                replay_release.set()
                await replay_task
            finally:
                replay_release.set()
                await dependency.aclose()
                reset_request_tenant_id(context_token)
            expected_writes = 1
        await _assert_write_counts(owner_factory, tenant_id, marker, expected_writes)
    finally:
        replay_release.set()
        await runtime_engine.dispose()
        await owner_engine.dispose()
