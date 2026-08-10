"""Real PostgreSQL proofs for agency-authorization lock order and actor binding."""

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api.v1.agency_auth import create_authorization
from app.core import database
from app.core.database import reset_request_security_credential, set_request_security_credential
from app.main import app
from app.models.audit import PlatformAuditLog
from app.models.auth_security import AuthSession
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    Organization,
    Permission,
    Role,
    Tenant,
    TenantType,
    account_roles,
    role_permissions,
)
from app.schemas.agency_auth import AuthorizationCreate
from app.services import agency_auth as agency_auth_service
from app.utils.security import create_access_token


@dataclass(frozen=True)
class AuthorizationPrincipal:
    agency_id: uuid.UUID
    client_id: uuid.UUID
    actor_id: uuid.UUID
    session_id: uuid.UUID
    role_id: uuid.UUID
    permission_id: uuid.UUID


def _runtime_dsn(migrated_pg_url: str) -> str:
    return migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "yimatong:yimatong@", "yimatong_app:yimatong_app@"
    )


async def _seed_principal(
    factory: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
) -> AuthorizationPrincipal:
    agency_id = agency_id or uuid.uuid4()
    client_id = client_id or uuid.uuid4()
    actor_id = uuid.uuid4()
    session_id = uuid.uuid4()
    role_id = uuid.uuid4()
    permission_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    async with factory() as db, db.begin():
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        db.add_all(
            [
                Tenant(
                    id=client_id,
                    name="并发授权品牌",
                    slug=f"agency-client-{client_id.hex}",
                    tenant_type=TenantType.brand,
                ),
                Tenant(
                    id=agency_id,
                    name="并发授权服务商",
                    slug=f"agency-provider-{agency_id.hex}",
                    tenant_type=TenantType.agency,
                ),
            ]
        )
        db.add(Organization(id=organization_id, tenant_id=client_id, name="品牌组织"))
        db.add(
            Account(
                id=actor_id,
                tenant_id=client_id,
                organization_id=organization_id,
                email=f"concurrent-{actor_id.hex}@example.com",
                hashed_password="not-used",
                name="并发授权管理员",
                auth_version=0,
            )
        )
        db.add(Role(id=role_id, tenant_id=client_id, name="admin"))
        db.add(Permission(id=permission_id, tenant_id=client_id, code="tenant:manage"))
        await db.flush()
        await db.execute(account_roles.insert().values(tenant_id=client_id, account_id=actor_id, role_id=role_id))
        await db.execute(
            role_permissions.insert().values(tenant_id=client_id, role_id=role_id, permission_id=permission_id)
        )
        db.add(
            AuthSession(
                id=session_id,
                account_id=actor_id,
                tenant_id=client_id,
                auth_version=0,
                current_refresh_jti=uuid.uuid4().hex,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    return AuthorizationPrincipal(agency_id, client_id, actor_id, session_id, role_id, permission_id)


async def _renew(
    conn: asyncpg.Connection,
    principal: AuthorizationPrincipal,
    authorization_id: uuid.UUID,
    scope: list[str],
) -> uuid.UUID:
    return await conn.fetchval(
        "SELECT renew_agency_authorization($1, $2, $3, $4::jsonb, $5, $6, NULL)",
        authorization_id,
        principal.agency_id,
        principal.client_id,
        json.dumps(scope),
        principal.actor_id,
        principal.session_id,
    )


async def _revoke(
    conn: asyncpg.Connection,
    principal: AuthorizationPrincipal,
    authorization_id: uuid.UUID,
) -> uuid.UUID | None:
    return await conn.fetchval(
        "SELECT revoke_agency_authorization($1, $2, $3, $4)",
        authorization_id,
        principal.client_id,
        principal.actor_id,
        principal.session_id,
    )


async def _runtime_transaction(migrated_pg_url: str) -> tuple[asyncpg.Connection, Transaction]:
    conn = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    transaction = conn.transaction()
    await transaction.start()
    return conn, transaction


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_admin_can_replace_seed_grant_without_a_grantor(migrated_pg_url: str) -> None:
    """The official demo seed's trusted grant can be narrowed by a live brand admin."""

    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    seeded_id = uuid.uuid4()
    replacement_scope = ["products", "pages"]

    try:
        async with owner_factory() as db, db.begin():
            db.add(
                AgencyAuthorization(
                    id=seeded_id,
                    agency_tenant_id=principal.agency_id,
                    client_tenant_id=principal.client_id,
                    scope=["products", "pages", "campaigns", "codes", "analytics"],
                    granted_by=None,
                    granted_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )

        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(principal.client_id)},
            )
            request = Request({"type": "http", "method": "POST", "path": "/api/v1/ops/authorizations"})
            request.state.session_id = str(principal.session_id)
            credential_token = set_request_security_credential("auth_session", str(principal.session_id))
            try:
                replacement = await create_authorization(
                    body=AuthorizationCreate(
                        agency_tenant_id=principal.agency_id,
                        scope=replacement_scope,
                    ),
                    request=request,
                    tenant_id=principal.client_id,
                    tenant_type="brand",
                    account_id=principal.actor_id,
                    db=db,
                    _permission=None,
                )
            finally:
                reset_request_security_credential(credential_token)

        async with owner_factory() as db:
            authorizations = list(
                (
                    await db.scalars(
                        select(AgencyAuthorization)
                        .where(
                            AgencyAuthorization.agency_tenant_id == principal.agency_id,
                            AgencyAuthorization.client_tenant_id == principal.client_id,
                        )
                        .order_by(AgencyAuthorization.created_at, AgencyAuthorization.id)
                    )
                ).all()
            )

        assert replacement.id != seeded_id
        assert replacement.scope == replacement_scope
        assert replacement.granted_by == principal.actor_id
        assert len(authorizations) == 2
        assert next(item for item in authorizations if item.id == seeded_id).status == "revoked"
        assert next(item for item in authorizations if item.id == replacement.id).status == "active"
        async with owner_factory() as db:
            audit = await db.scalar(
                select(PlatformAuditLog).where(
                    PlatformAuditLog.target_tenant_id == str(principal.client_id),
                    PlatformAuditLog.action == "agency_authorization_granted",
                    PlatformAuditLog.resource == f"agency_authorization:{replacement.id}",
                )
            )
        assert audit is not None
        assert audit.operator_id == str(principal.actor_id)
        assert audit.details == {
            "agency_tenant_id": str(principal.agency_id),
            "scope": replacement_scope,
        }
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_seed_grant_replacement_rolls_back_when_audit_fails(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    seeded_id = uuid.uuid4()

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.api.v1.agency_auth.write_audit_log", fail_audit)
    try:
        async with owner_factory() as db, db.begin():
            db.add(
                AgencyAuthorization(
                    id=seeded_id,
                    agency_tenant_id=principal.agency_id,
                    client_tenant_id=principal.client_id,
                    scope=["products", "pages", "campaigns", "codes", "analytics"],
                    granted_by=None,
                    granted_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )

        request = Request({"type": "http", "method": "POST", "path": "/api/v1/ops/authorizations"})
        request.state.session_id = str(principal.session_id)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            async with runtime_factory() as db, db.begin():
                await db.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(principal.client_id)},
                )
                credential_token = set_request_security_credential("auth_session", str(principal.session_id))
                try:
                    await create_authorization(
                        body=AuthorizationCreate(
                            agency_tenant_id=principal.agency_id,
                            scope=["products", "pages"],
                        ),
                        request=request,
                        tenant_id=principal.client_id,
                        tenant_type="brand",
                        account_id=principal.actor_id,
                        db=db,
                        _permission=None,
                    )
                finally:
                    reset_request_security_credential(credential_token)

        async with owner_factory() as db:
            authorizations = list(
                (
                    await db.scalars(
                        select(AgencyAuthorization).where(
                            AgencyAuthorization.agency_tenant_id == principal.agency_id,
                            AgencyAuthorization.client_tenant_id == principal.client_id,
                        )
                    )
                ).all()
            )
            audit_count = await db.scalar(
                select(func.count())
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.target_tenant_id == str(principal.client_id),
                    PlatformAuditLog.action == "agency_authorization_granted",
                )
            )

        assert len(authorizations) == 1
        assert authorizations[0].id == seeded_id
        assert authorizations[0].status == "active"
        assert audit_count == 0
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
@pytest.mark.parametrize("agency_sorts_first", [True, False])
async def test_concurrent_renew_serializes_in_both_tenant_uuid_orders(
    migrated_pg_url: str,
    agency_sorts_first: bool,
):
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ordered_ids = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    agency_id, client_id = ordered_ids if agency_sorts_first else tuple(reversed(ordered_ids))
    principal = await _seed_principal(factory, agency_id=agency_id, client_id=client_id)
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_has_locks = asyncio.Event()
    release_first = asyncio.Event()

    async def first_renew() -> uuid.UUID:
        conn, transaction = await _runtime_transaction(migrated_pg_url)
        transaction_finished = False
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(client_id))
            result = await _renew(conn, principal, first_id, ["pages"])
            first_has_locks.set()
            await release_first.wait()
            await transaction.commit()
            transaction_finished = True
            return result
        finally:
            if not transaction_finished:
                await transaction.rollback()
            await conn.close()

    async def second_renew() -> uuid.UUID:
        conn, transaction = await _runtime_transaction(migrated_pg_url)
        transaction_finished = False
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(client_id))
            result = await _renew(conn, principal, second_id, ["pages", "campaigns"])
            await transaction.commit()
            transaction_finished = True
            return result
        finally:
            if not transaction_finished:
                await transaction.rollback()
            await conn.close()

    try:
        first_task = asyncio.create_task(first_renew())
        await asyncio.wait_for(first_has_locks.wait(), timeout=3)
        second_task = asyncio.create_task(second_renew())
        await asyncio.sleep(0.1)
        assert not second_task.done()
        release_first.set()
        assert await asyncio.wait_for(first_task, timeout=3) == first_id
        assert await asyncio.wait_for(second_task, timeout=3) == second_id

        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            authorizations = (
                (
                    await db.execute(
                        select(AgencyAuthorization)
                        .where(
                            AgencyAuthorization.agency_tenant_id == agency_id,
                            AgencyAuthorization.client_tenant_id == client_id,
                        )
                        .order_by(AgencyAuthorization.created_at)
                    )
                )
                .scalars()
                .all()
            )
            assert {authorization.id for authorization in authorizations} == {first_id, second_id}
            assert sum(authorization.status == "active" for authorization in authorizations) == 1
            assert (
                next(authorization for authorization in authorizations if authorization.status == "active").id
                == second_id
            )
    finally:
        release_first.set()
        await engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_expired_client_plan_blocks_renew_without_writes_but_allows_revoke(migrated_pg_url: str):
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(factory)
    original_id = uuid.uuid4()
    rejected_id = uuid.uuid4()

    conn, transaction = await _runtime_transaction(migrated_pg_url)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
        assert await _renew(conn, principal, original_id, ["pages"]) == original_id
        await transaction.commit()
    finally:
        await conn.close()

    async with factory() as db, db.begin():
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        client = await db.get(Tenant, principal.client_id)
        assert client is not None
        client.plan_expires_at = datetime.now(UTC) - timedelta(minutes=1)

    conn, transaction = await _runtime_transaction(migrated_pg_url)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
        with pytest.raises(asyncpg.RaiseError, match="endpoints or grantor session are not live"):
            await _renew(conn, principal, rejected_id, ["campaigns"])
        await transaction.rollback()
    finally:
        await conn.close()

    conn, transaction = await _runtime_transaction(migrated_pg_url)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
        assert await _revoke(conn, principal, original_id) == original_id
        await transaction.commit()
    finally:
        await conn.close()

    async with factory() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert (
            await db.scalar(
                select(func.count()).select_from(AgencyAuthorization).where(AgencyAuthorization.id == rejected_id)
            )
            == 0
        )
        authorization = await db.get(AgencyAuthorization, original_id)
        assert authorization is not None
        assert authorization.status == "revoked"
    await engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_expired_brand_can_revoke_through_http_but_cannot_create(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    runtime_engine = create_async_engine(runtime_url)
    control_engine = create_async_engine(control_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    authorization_id = uuid.uuid4()

    try:
        conn, transaction = await _runtime_transaction(migrated_pg_url)
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
            assert await _renew(conn, principal, authorization_id, ["pages"]) == authorization_id
            await transaction.commit()
        finally:
            await conn.close()

        async with owner_factory() as db, db.begin():
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            client_tenant = await db.get(Tenant, principal.client_id)
            assert client_tenant is not None
            client_tenant.plan_expires_at = datetime.now(UTC) - timedelta(minutes=1)

        monkeypatch.setattr(database, "async_session_factory", runtime_factory)
        monkeypatch.setattr(database, "control_session_factory", control_factory)
        monkeypatch.setattr(database, "_is_pg", True)
        access_token = create_access_token(
            str(principal.client_id),
            str(principal.actor_id),
            "admin",
            "brand",
            extra={"sid": str(principal.session_id), "auth_version": 0},
        )
        headers = {"Authorization": f"Bearer {access_token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            blocked_create = await client.post(
                "/api/v1/ops/authorizations",
                headers=headers,
                json={"agency_tenant_id": str(principal.agency_id), "scope": ["campaigns"]},
            )
            assert blocked_create.status_code == 403
            assert blocked_create.json()["code"] == "TENANT_PLAN_EXPIRED"

            async with owner_factory() as db:
                await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AgencyAuthorization)
                        .where(
                            AgencyAuthorization.agency_tenant_id == principal.agency_id,
                            AgencyAuthorization.client_tenant_id == principal.client_id,
                        )
                    )
                    == 1
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(PlatformAuditLog)
                        .where(
                            PlatformAuditLog.target_tenant_id == str(principal.client_id),
                            PlatformAuditLog.action == "agency_authorization_granted",
                        )
                    )
                    == 0
                )

            revoked = await client.delete(
                f"/api/v1/ops/authorizations/{authorization_id}",
                headers=headers,
            )
            assert revoked.status_code == 204, revoked.text

        async with owner_factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            authorization = await db.get(AgencyAuthorization, authorization_id)
            assert authorization is not None
            assert authorization.status == "revoked"
            revoke_audits = list(
                (
                    await db.scalars(
                        select(PlatformAuditLog).where(
                            PlatformAuditLog.target_tenant_id == str(principal.client_id),
                            PlatformAuditLog.action == "agency_authorization_revoked",
                            PlatformAuditLog.resource == f"agency_authorization:{authorization_id}",
                        )
                    )
                ).all()
            )
            assert len(revoke_audits) == 1
            assert revoke_audits[0].operator_id == str(principal.actor_id)
    finally:
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_expired_active_authorization_delete_commits_expiry_and_releases_active_slot(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    runtime_engine = create_async_engine(runtime_url)
    control_engine = create_async_engine(control_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    expired_id = uuid.uuid4()

    try:
        async with owner_factory() as db, db.begin():
            db.add(
                AgencyAuthorization(
                    id=expired_id,
                    agency_tenant_id=principal.agency_id,
                    client_tenant_id=principal.client_id,
                    scope=["pages"],
                    granted_by=principal.actor_id,
                    granted_at=datetime.now(UTC) - timedelta(minutes=2),
                    expires_at=datetime.now(UTC) + timedelta(seconds=1),
                )
            )
        await asyncio.sleep(1.1)

        monkeypatch.setattr(database, "async_session_factory", runtime_factory)
        monkeypatch.setattr(database, "control_session_factory", control_factory)
        monkeypatch.setattr(database, "_is_pg", True)
        access_token = create_access_token(
            str(principal.client_id),
            str(principal.actor_id),
            "admin",
            "brand",
            extra={"sid": str(principal.session_id), "auth_version": 0},
        )
        headers = {"Authorization": f"Bearer {access_token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_delete = await client.delete(
                f"/api/v1/ops/authorizations/{expired_id}",
                headers=headers,
            )
            assert first_delete.status_code == 204, first_delete.text

            replacement = await client.post(
                "/api/v1/ops/authorizations",
                headers=headers,
                json={"agency_tenant_id": str(principal.agency_id), "scope": ["campaigns"]},
            )
            assert replacement.status_code == 201, replacement.text

            repeated_delete = await client.delete(
                f"/api/v1/ops/authorizations/{expired_id}",
                headers=headers,
            )
            assert repeated_delete.status_code == 404

            missing_delete = await client.delete(
                f"/api/v1/ops/authorizations/{uuid.uuid4()}",
                headers=headers,
            )
            assert missing_delete.status_code == 404

        async with owner_factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            expired = await db.get(AgencyAuthorization, expired_id)
            assert expired is not None
            assert expired.status == "expired"
            assert expired.revoked_at is None

            active_rows = list(
                (
                    await db.scalars(
                        select(AgencyAuthorization).where(
                            AgencyAuthorization.agency_tenant_id == principal.agency_id,
                            AgencyAuthorization.client_tenant_id == principal.client_id,
                            AgencyAuthorization.status == "active",
                        )
                    )
                ).all()
            )
            assert len(active_rows) == 1
            assert active_rows[0].id == uuid.UUID(replacement.json()["id"])

            expiry_audits = list(
                (
                    await db.scalars(
                        select(PlatformAuditLog).where(
                            PlatformAuditLog.target_tenant_id == str(principal.client_id),
                            PlatformAuditLog.action == "agency_authorization_expired",
                            PlatformAuditLog.resource == f"agency_authorization:{expired_id}",
                        )
                    )
                ).all()
            )
            assert len(expiry_audits) == 1
            assert expiry_audits[0].operator_id == str(principal.actor_id)
            assert expiry_audits[0].details == {
                "agency_tenant_id": str(principal.agency_id),
                "scope": ["pages"],
                "resulting_status": "expired",
            }
    finally:
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_expired_authorization_transition_rolls_back_when_audit_fails(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    runtime_engine = create_async_engine(runtime_url)
    control_engine = create_async_engine(control_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    expired_id = uuid.uuid4()

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    try:
        async with owner_factory() as db, db.begin():
            db.add(
                AgencyAuthorization(
                    id=expired_id,
                    agency_tenant_id=principal.agency_id,
                    client_tenant_id=principal.client_id,
                    scope=["pages"],
                    granted_by=principal.actor_id,
                    granted_at=datetime.now(UTC) - timedelta(minutes=2),
                    expires_at=datetime.now(UTC) + timedelta(seconds=1),
                )
            )
        await asyncio.sleep(1.1)

        monkeypatch.setattr(database, "async_session_factory", runtime_factory)
        monkeypatch.setattr(database, "control_session_factory", control_factory)
        monkeypatch.setattr(database, "_is_pg", True)
        monkeypatch.setattr("app.api.v1.agency_auth.write_audit_log", fail_audit)
        access_token = create_access_token(
            str(principal.client_id),
            str(principal.actor_id),
            "admin",
            "brand",
            extra={"sid": str(principal.session_id), "auth_version": 0},
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.delete(
                    f"/api/v1/ops/authorizations/{expired_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

        async with owner_factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            authorization = await db.get(AgencyAuthorization, expired_id)
            assert authorization is not None
            assert authorization.status == "active"
            assert authorization.revoked_at is None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(PlatformAuditLog)
                    .where(PlatformAuditLog.resource == f"agency_authorization:{expired_id}")
                )
                == 0
            )
    finally:
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_old_acting_http_token_is_denied_after_active_delete_response(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed DELETE is the revocation boundary for an existing acting token."""

    owner_engine = create_async_engine(migrated_pg_url)
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    runtime_engine = create_async_engine(runtime_url)
    control_engine = create_async_engine(control_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(owner_factory)
    agency_account_id = uuid.uuid4()
    agency_session_id = uuid.uuid4()

    try:
        async with owner_factory() as db, db.begin():
            organization = Organization(
                id=uuid.uuid4(),
                tenant_id=principal.agency_id,
                name="代运营组织",
            )
            role = Role(id=uuid.uuid4(), tenant_id=principal.agency_id, name="admin")
            db.add_all(
                [
                    organization,
                    role,
                    Account(
                        id=agency_account_id,
                        tenant_id=principal.agency_id,
                        organization_id=organization.id,
                        email=f"acting-{agency_account_id.hex}@example.com",
                        hashed_password="not-used",
                        name="代运营管理员",
                        auth_version=0,
                    ),
                ]
            )
            await db.flush()
            await db.execute(
                account_roles.insert().values(
                    tenant_id=principal.agency_id,
                    account_id=agency_account_id,
                    role_id=role.id,
                )
            )
            db.add(
                AuthSession(
                    id=agency_session_id,
                    account_id=agency_account_id,
                    tenant_id=principal.agency_id,
                    auth_version=0,
                    current_refresh_jti=uuid.uuid4().hex,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )

        monkeypatch.setattr(database, "async_session_factory", runtime_factory)
        monkeypatch.setattr(database, "control_session_factory", control_factory)
        monkeypatch.setattr(database, "_is_pg", True)
        monkeypatch.setattr(agency_auth_service, "_is_pg", True)
        brand_token = create_access_token(
            str(principal.client_id),
            str(principal.actor_id),
            "admin",
            "brand",
            extra={"sid": str(principal.session_id), "auth_version": 0},
        )
        agency_token = create_access_token(
            str(principal.agency_id),
            str(agency_account_id),
            "admin",
            "agency",
            extra={"sid": str(agency_session_id), "auth_version": 0},
        )
        brand_headers = {"Authorization": f"Bearer {brand_token}"}
        agency_headers = {"Authorization": f"Bearer {agency_token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/ops/authorizations",
                headers=brand_headers,
                json={"agency_tenant_id": str(principal.agency_id), "scope": ["products"]},
            )
            assert created.status_code == 201, created.text
            authorization_id = uuid.UUID(created.json()["id"])

            switched = await client.post(
                "/api/v1/agency/switch-context",
                headers=agency_headers,
                json={"client_tenant_id": str(principal.client_id)},
            )
            assert switched.status_code == 200, switched.text
            acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}
            assert (await client.get("/api/v1/products", headers=acting_headers)).status_code == 200

            deleted = await client.delete(
                f"/api/v1/ops/authorizations/{authorization_id}",
                headers=brand_headers,
            )
            assert deleted.status_code == 204, deleted.text

            async with owner_factory() as db:
                await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                authorization = await db.get(AgencyAuthorization, authorization_id)
                assert authorization is not None
                assert authorization.status == "revoked"
                assert authorization.revoked_at is not None
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AgencyAuthorization)
                        .where(
                            AgencyAuthorization.agency_tenant_id == principal.agency_id,
                            AgencyAuthorization.client_tenant_id == principal.client_id,
                            AgencyAuthorization.status == "active",
                        )
                    )
                    == 0
                )

            old_acting_read = await client.get("/api/v1/products", headers=acting_headers)
            assert old_acting_read.status_code == 403, old_acting_read.text
            assert old_acting_read.json()["detail"] == "代运营授权已失效"
    finally:
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.acceptance
@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["renew", "revoke"])
@pytest.mark.parametrize("principal_change", ["session_revoked", "permission_removed"])
async def test_waiting_transition_revalidates_principal_after_lock(
    migrated_pg_url: str,
    operation: str,
    principal_change: str,
):
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    principal = await _seed_principal(factory)
    original_id = uuid.uuid4()
    rejected_id = uuid.uuid4()

    conn, transaction = await _runtime_transaction(migrated_pg_url)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
        assert await _renew(conn, principal, original_id, ["pages"]) == original_id
        await transaction.commit()
    finally:
        await conn.close()

    blocker = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    blocker_transaction = blocker.transaction()
    await blocker_transaction.start()
    blocker_transaction_finished = False
    transition_started = asyncio.Event()

    async def waiting_transition() -> None:
        runtime_conn, runtime_transaction = await _runtime_transaction(migrated_pg_url)
        try:
            await runtime_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(principal.client_id))
            transition_started.set()
            with pytest.raises(asyncpg.RaiseError, match="actor session is not live|grantor session are not live"):
                if operation == "renew":
                    await _renew(runtime_conn, principal, rejected_id, ["campaigns"])
                else:
                    await _revoke(runtime_conn, principal, original_id)
            await runtime_transaction.rollback()
        finally:
            await runtime_conn.close()

    try:
        await blocker.execute(
            "SELECT id FROM tenants WHERE id = ANY($1::uuid[]) ORDER BY id::text FOR UPDATE",
            [principal.agency_id, principal.client_id],
        )
        transition_task = asyncio.create_task(waiting_transition())
        await asyncio.wait_for(transition_started.wait(), timeout=3)
        await asyncio.sleep(0.1)
        assert not transition_task.done()
        if principal_change == "session_revoked":
            await blocker.execute("UPDATE auth_sessions SET revoked_at = now() WHERE id = $1", principal.session_id)
        else:
            await blocker.execute(
                "DELETE FROM role_permissions WHERE tenant_id = $1 AND role_id = $2 AND permission_id = $3",
                principal.client_id,
                principal.role_id,
                principal.permission_id,
            )
        await blocker_transaction.commit()
        blocker_transaction_finished = True
        await asyncio.wait_for(transition_task, timeout=3)

        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            original = await db.get(AgencyAuthorization, original_id)
            assert original is not None
            assert original.status == "active"
            assert (
                await db.scalar(
                    select(func.count()).select_from(AgencyAuthorization).where(AgencyAuthorization.id == rejected_id)
                )
                == 0
            )
    finally:
        if not blocker_transaction_finished:
            await blocker_transaction.rollback()
        await blocker.close()
        await engine.dispose()
