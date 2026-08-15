"""PostgreSQL gates for canonical consumer-detail fixed-role grants."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.core import database
from app.main import app
from app.middleware.rate_limit import rate_limiter
from app.services.redis_cache import AsyncRedisCache
from app.utils.security import create_access_token
from tests.test_acceptance.test_cli_identity_reconciliation import _run_cli, _run_seed_demo_script
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "u6h1f2a3b4c5"
CANDIDATE_REVISION = "u6i0a1b2c3d4"


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


async def _insert_tenant(conn: asyncpg.Connection, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, organization_id = uuid7(), uuid7()
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        tenant_id,
        f"consumer role {label}",
        f"consumer-role-{label}-{tenant_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        organization_id,
        tenant_id,
        f"consumer role {label} org",
    )
    return tenant_id, organization_id


async def _insert_role(conn: asyncpg.Connection, tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    role_id = uuid7()
    await conn.execute(
        "INSERT INTO roles(id,tenant_id,name,description,created_at,updated_at) VALUES($1,$2,$3,$4,now(),now())",
        role_id,
        tenant_id,
        name,
        f"{name} role",
    )
    return role_id


async def test_consumer_detail_grants_upgrade_and_downgrade_only_owned_facts(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
        assert initial_revision
    finally:
        await owner.close()

    try:
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            existing_tenant, _ = await _insert_tenant(owner, "existing")
            created_tenant, _ = await _insert_tenant(owner, "created")
            roles: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
            for tenant_id in (existing_tenant, created_tenant):
                for role_name in ("admin", "operator", "viewer", "campaign_manager"):
                    roles[(tenant_id, role_name)] = await _insert_role(owner, tenant_id, role_name)

            existing_permission = uuid7()
            await owner.execute(
                "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
                "VALUES($1,$2,'consumer:detail','existing',now(),now())",
                existing_permission,
                existing_tenant,
            )
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                existing_tenant,
                roles[(existing_tenant, "admin")],
                existing_permission,
            )
        finally:
            await owner.close()

        _alembic(migrated_pg_url, "upgrade", CANDIDATE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            for tenant_id in (existing_tenant, created_tenant):
                permission_id = await owner.fetchval(
                    "SELECT id FROM permissions WHERE tenant_id=$1 AND code='consumer:detail'", tenant_id
                )
                assert permission_id is not None
                mapped_roles = {
                    row["name"]
                    for row in await owner.fetch(
                        "SELECT role.name FROM roles AS role JOIN role_permissions AS mapping "
                        "ON mapping.tenant_id=role.tenant_id AND mapping.role_id=role.id "
                        "WHERE role.tenant_id=$1 AND mapping.permission_id=$2",
                        tenant_id,
                        permission_id,
                    )
                }
                assert mapped_roles == {"admin", "operator"}

            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_detail_role_grant_backfills WHERE tenant_id=$1", existing_tenant
                )
                == 1
            )
            assert not await owner.fetchval(
                "SELECT permission_created FROM consumer_detail_role_grant_backfills WHERE tenant_id=$1",
                existing_tenant,
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_detail_role_grant_backfills WHERE tenant_id=$1", created_tenant
                )
                == 2
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_detail_role_grant_backfills WHERE tenant_id=$1 "
                    "AND permission_created",
                    created_tenant,
                )
                == 1
            )
            assert await owner.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='public.consumer_detail_role_grant_backfills'::regclass"
            )
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.consumer_detail_role_grant_backfills','SELECT')"
            )
        finally:
            await owner.close()

        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3)",
                existing_tenant,
                roles[(existing_tenant, "admin")],
                existing_permission,
            )
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3)",
                existing_tenant,
                roles[(existing_tenant, "operator")],
                existing_permission,
            )
            assert await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM permissions WHERE tenant_id=$1 AND code='consumer:detail')",
                existing_tenant,
            )
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM permissions WHERE tenant_id=$1 AND code='consumer:detail')",
                created_tenant,
            )
            assert await owner.fetchval("SELECT to_regclass('public.consumer_detail_role_grant_backfills')") is None
        finally:
            await owner.close()
    finally:
        _alembic(migrated_pg_url, "upgrade", initial_revision)


async def _seed_http_actor_graph(owner: asyncpg.Connection) -> dict[str, object]:
    tenant_id, organization_id = await _insert_tenant(owner, "http")
    other_tenant_id, other_organization_id = await _insert_tenant(owner, "other")
    permission_id = uuid7()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
        "VALUES($1,$2,'consumer:detail','canonical',now(),now())",
        permission_id,
        tenant_id,
    )
    result: dict[str, object] = {
        "tenant": tenant_id,
        "other_tenant": other_tenant_id,
        "permission": permission_id,
    }
    for role_name in ("admin", "operator", "viewer"):
        role_id = await _insert_role(owner, tenant_id, role_name)
        account_id, session_id = uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
            "is_active,auth_version,must_change_password,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
            account_id,
            tenant_id,
            organization_id,
            f"{role_name}-{account_id.hex[:8]}@test.local",
            f"{role_name} actor",
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            tenant_id,
            account_id,
            role_id,
        )
        if role_name != "viewer":
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                tenant_id,
                role_id,
                permission_id,
            )
        await owner.execute(
            "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
            "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            session_id,
            account_id,
            tenant_id,
            uuid.uuid4().hex,
        )
        result[role_name] = {"role": role_id, "account": account_id, "session": session_id}

    other_role = await _insert_role(owner, other_tenant_id, "admin")
    other_permission = uuid7()
    other_account, other_session = uuid7(), uuid7()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
        "VALUES($1,$2,'consumer:detail','canonical',now(),now())",
        other_permission,
        other_tenant_id,
    )
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x','other actor',0,true,0,false,now(),now())",
        other_account,
        other_tenant_id,
        other_organization_id,
        f"other-{other_account.hex[:8]}@test.local",
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        other_tenant_id,
        other_account,
        other_role,
    )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        other_tenant_id,
        other_role,
        other_permission,
    )
    await owner.execute(
        "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
        "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        other_session,
        other_account,
        other_tenant_id,
        uuid.uuid4().hex,
    )
    result["other"] = {"role": other_role, "account": other_account, "session": other_session}
    return result


def _token(graph: dict[str, object], role_name: str) -> str:
    actor = graph[role_name]
    assert isinstance(actor, dict)
    return create_access_token(
        str(graph["tenant"]),
        str(actor["account"]),
        role_name,
        "brand",
        extra={"sid": str(actor["session"]), "auth_version": 0},
    )


async def test_consumer_creation_http_role_permission_and_tenant_boundaries(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    async with owner.transaction():
        graph = await _seed_http_actor_graph(owner)
    runtime_engine = create_async_engine(
        _runtime_dsn(migrated_pg_url).replace("postgresql://", "postgresql+asyncpg://")
    )
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]
    before = await owner.fetchval("SELECT count(*) FROM consumer_profiles WHERE tenant_id=$1", graph["tenant"])
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            created_ids: list[uuid.UUID] = []
            for role_name in ("admin", "operator"):
                response = await client.post(
                    "/api/v1/members/consumers",
                    json={},
                    headers={"Authorization": f"Bearer {_token(graph, role_name)}"},
                )
                assert response.status_code == 201, response.text
                created_id = uuid.UUID(response.json()["id"])
                created_ids.append(created_id)
                actor = graph[role_name]
                assert isinstance(actor, dict)
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM platform_audit_log WHERE action='consumer_profile_created' "
                        "AND target_tenant_id=$1 AND resource=$2 AND operator_id=$3",
                        str(graph["tenant"]),
                        f"consumer_profile:{created_id}",
                        str(actor["account"]),
                    )
                    == 1
                )

            viewer = await client.post(
                "/api/v1/members/consumers",
                json={},
                headers={"Authorization": f"Bearer {_token(graph, 'viewer')}"},
            )
            assert viewer.status_code == 403, viewer.text

            operator = graph["operator"]
            assert isinstance(operator, dict)
            await owner.execute(
                "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
                graph["tenant"],
                operator["role"],
                graph["permission"],
            )
            revoked_permission = await client.post(
                "/api/v1/members/consumers",
                json={},
                headers={"Authorization": f"Bearer {_token(graph, 'operator')}"},
            )
            assert revoked_permission.status_code == 403, revoked_permission.text

            admin = graph["admin"]
            assert isinstance(admin, dict)
            await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", admin["session"])
            revoked_session = await client.post(
                "/api/v1/members/consumers",
                json={},
                headers={"Authorization": f"Bearer {_token(graph, 'admin')}"},
            )
            assert revoked_session.status_code == 401, revoked_session.text

        runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
        try:
            other = graph["other"]
            assert isinstance(other, dict)
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["other_tenant"]))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.fetchrow(
                        "SELECT * FROM create_anonymous_consumer_profile($1,$2,$3,$4)",
                        graph["tenant"],
                        other["session"],
                        uuid7(),
                        uuid7(),
                    )
        finally:
            await runtime.close()

        assert (
            await owner.fetchval("SELECT count(*) FROM consumer_profiles WHERE tenant_id=$1", graph["tenant"])
            == before + 2
        )
        assert (
            await owner.fetchval("SELECT count(*) FROM consumer_profiles WHERE tenant_id=$1", graph["other_tenant"])
            == 0
        )
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_official_all_and_rich_seed_replay_canonical_consumer_detail_permissions(
    migrated_pg_url: str,
) -> None:
    for _ in range(2):
        await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
    for _ in range(2):
        await asyncio.to_thread(_run_seed_demo_script, migrated_pg_url, "generate", "--target", "demo")

    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        rows = await owner.fetch(
            "SELECT role.name,role.id AS role_id,permission.id AS permission_id "
            "FROM tenants AS tenant JOIN roles AS role ON role.tenant_id=tenant.id "
            "LEFT JOIN role_permissions AS mapping ON mapping.tenant_id=role.tenant_id AND mapping.role_id=role.id "
            "LEFT JOIN permissions AS permission ON permission.tenant_id=mapping.tenant_id "
            "AND permission.id=mapping.permission_id AND permission.code='consumer:detail' "
            "WHERE tenant.slug='demo' AND role.name IN ('admin','operator','viewer','distributor','store_guide') "
            "ORDER BY role.name"
        )
        permission_links = {
            row["name"]: (row["role_id"], row["permission_id"]) for row in rows if row["permission_id"] is not None
        }
        assert set(permission_links) == {"admin", "operator"}
        assert permission_links["admin"][1] == permission_links["operator"][1]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM permissions AS permission JOIN tenants AS tenant "
                "ON tenant.id=permission.tenant_id WHERE tenant.slug='demo' "
                "AND permission.code='consumer:detail'"
            )
            == 1
        )
    finally:
        await owner.close()
