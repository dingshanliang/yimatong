"""Real-PostgreSQL contract for hashed external API credentials."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import struct
import subprocess
import sys
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.main import app
from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS
from app.utils.security import create_access_token

BACKEND_DIR = Path(__file__).resolve().parents[2]
ADMIN_DSN = os.getenv("ACCEPTANCE_PG_ADMIN_URL", "postgresql://yimatong:yimatong@localhost:5433/postgres")
TENANT_ID = uuid.UUID("11111111-1111-7111-8111-111111111111")
ACTOR_ID = uuid.UUID("31111111-1111-7111-8111-111111111111")
SESSION_ID = uuid.UUID("61111111-1111-7111-8111-111111111111")


@dataclass(frozen=True, slots=True)
class _HttpPrincipal:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    session_id: uuid.UUID


def _owner_dsn(database_name: str, *, sqlalchemy: bool = False) -> str:
    scheme = "postgresql+asyncpg" if sqlalchemy else "postgresql"
    return f"{scheme}://yimatong:yimatong@localhost:5433/{database_name}"


def _runtime_dsn(database_name: str) -> str:
    return f"postgresql://yimatong_app:yimatong_app@localhost:5433/{database_name}"


def _runtime_sqlalchemy_url(migrated_pg_url: str) -> str:
    return migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _control_sqlalchemy_url(migrated_pg_url: str) -> str:
    return migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")


def _run_alembic(
    database_name: str,
    direction: str,
    target: str,
    *,
    migration_key: str | None = None,
    aes_key: bytes | None = None,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    database_url = _owner_dsn(database_name, sqlalchemy=True)
    env.update(
        database_url=database_url,
        migration_database_url=database_url,
        control_database_url=database_url,
    )
    if migration_key is None:
        env.pop("API_KEY_MIGRATION_KEY", None)
    else:
        env["API_KEY_MIGRATION_KEY"] = migration_key
    if aes_key is None:
        env.pop("AES_MASTER_KEY_V1", None)
    else:
        env["AES_MASTER_KEY_V1"] = aes_key.hex()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, target],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if expect_success and result.returncode != 0:
        pytest.fail(f"alembic {direction} {target} failed:\n{result.stdout}\n{result.stderr}")
    if not expect_success and result.returncode == 0:
        pytest.fail(f"alembic {direction} {target} unexpectedly succeeded")
    return result


@contextmanager
def _owned_database():
    database_name = f"yimatong_acceptance_api_key_stage_{os.getpid()}_{secrets.token_hex(4)}"

    async def create() -> None:
        conn = await asyncpg.connect(ADMIN_DSN)
        try:
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", database_name)
            await conn.execute(f'CREATE DATABASE "{database_name}" OWNER yimatong')
        finally:
            await conn.close()

    async def drop() -> None:
        conn = await asyncpg.connect(ADMIN_DSN)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", database_name)
            assert await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=$1", database_name) == 0
        finally:
            await conn.close()

    asyncio.run(create())
    try:
        yield database_name
    finally:
        asyncio.run(drop())


async def _seed_actor(conn: asyncpg.Connection, slug: str) -> None:
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,plan_expires_at) "
            "VALUES ($1,'API key contract',$2,'active','pro','brand',now()+interval '30 days')",
            TENANT_ID,
            slug,
        )
        await conn.execute(
            "INSERT INTO organizations (id,tenant_id,name) VALUES ($1,$2,'Root')",
            uuid.UUID("21111111-1111-7111-8111-111111111111"),
            TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO accounts "
            "(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts) "
            "VALUES ($1,$2,$3,'api-key-contract@example.invalid','unused','Admin',true,0,false,0)",
            ACTOR_ID,
            TENANT_ID,
            uuid.UUID("21111111-1111-7111-8111-111111111111"),
        )
        role_id = uuid.UUID("41111111-1111-7111-8111-111111111111")
        permission_id = uuid.UUID("51111111-1111-7111-8111-111111111111")
        await conn.execute("INSERT INTO roles (id,tenant_id,name) VALUES ($1,$2,'admin')", role_id, TENANT_ID)
        await conn.execute(
            "INSERT INTO permissions (id,tenant_id,code) VALUES ($1,$2,'tenant:manage')",
            permission_id,
            TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO account_roles (tenant_id,account_id,role_id) VALUES ($1,$2,$3)",
            TENANT_ID,
            ACTOR_ID,
            role_id,
        )
        await conn.execute(
            "INSERT INTO role_permissions (tenant_id,role_id,permission_id) VALUES ($1,$2,$3)",
            TENANT_ID,
            role_id,
            permission_id,
        )
        await conn.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at) "
            "VALUES ($1,$2,$3,0,$4,now()+interval '1 day')",
            SESSION_ID,
            ACTOR_ID,
            TENANT_ID,
            f"api-key-contract-{uuid.uuid4()}",
        )


async def _seed_http_principal(conn: asyncpg.Connection, slug: str) -> _HttpPrincipal:
    principal = _HttpPrincipal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    organization_id, role_id, permission_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,plan_expires_at) "
            "VALUES ($1,'API key HTTP contract',$2,'active','pro','brand',now()+interval '30 days')",
            principal.tenant_id,
            slug,
        )
        await conn.execute(
            "INSERT INTO organizations (id,tenant_id,name) VALUES ($1,$2,'Root')",
            organization_id,
            principal.tenant_id,
        )
        await conn.execute(
            "INSERT INTO accounts "
            "(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts) "
            "VALUES ($1,$2,$3,$4,'unused','Admin',true,0,false,0)",
            principal.actor_id,
            principal.tenant_id,
            organization_id,
            f"api-key-http-{principal.actor_id.hex}@example.invalid",
        )
        await conn.execute(
            "INSERT INTO roles (id,tenant_id,name) VALUES ($1,$2,'admin')",
            role_id,
            principal.tenant_id,
        )
        await conn.execute(
            "INSERT INTO permissions (id,tenant_id,code) VALUES ($1,$2,'tenant:manage')",
            permission_id,
            principal.tenant_id,
        )
        await conn.execute(
            "INSERT INTO account_roles (tenant_id,account_id,role_id) VALUES ($1,$2,$3)",
            principal.tenant_id,
            principal.actor_id,
            role_id,
        )
        await conn.execute(
            "INSERT INTO role_permissions (tenant_id,role_id,permission_id) VALUES ($1,$2,$3)",
            principal.tenant_id,
            role_id,
            permission_id,
        )
        await conn.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at) "
            "VALUES ($1,$2,$3,0,$4,now()+interval '1 day')",
            principal.session_id,
            principal.actor_id,
            principal.tenant_id,
            f"api-key-http-{uuid.uuid4()}",
        )
    return principal


async def _seed_coupon_claim(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    campaign_id, benefit_id, claim_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO campaigns "
            "(id,tenant_id,name,campaign_type,status,start_at,end_at,rules_json) "
            "VALUES ($1,$2,$3,'coupon','active','2026-01-01T00:00:00','2027-12-31T23:59:59','{}'::json)",
            campaign_id,
            tenant_id,
            f"API key race {campaign_id}",
        )
        await conn.execute(
            "INSERT INTO benefits "
            "(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,status) "
            "VALUES ($1,$2,$3,'Race coupon','platform_coupon','{}'::json,10,1,1,'active')",
            benefit_id,
            tenant_id,
            campaign_id,
        )
        await conn.execute(
            "INSERT INTO benefit_claims "
            "(id,tenant_id,benefit_id,campaign_id,consumer_id,idempotency_key,claim_type,status,delivery_status) "
            "VALUES ($1,$2,$3,$4,$5,$6,'claim','success','not_required')",
            claim_id,
            tenant_id,
            benefit_id,
            campaign_id,
            f"consumer-{claim_id}",
            f"idem-{claim_id}",
        )
    return claim_id


def _admin_headers(principal: _HttpPrincipal) -> dict[str, str]:
    token = create_access_token(
        str(principal.tenant_id),
        str(principal.actor_id),
        "admin",
        "brand",
        extra={"sid": str(principal.session_id), "auth_version": 0},
    )
    return {"Authorization": f"Bearer {token}"}


def _lifecycle_headers(admin_headers: dict[str, str]) -> dict[str, str]:
    return {**admin_headers, "Idempotency-Key": str(uuid.uuid4())}


async def _set_tenant(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(TENANT_ID))


def _app_escrow(api_key_id: uuid.UUID, secret: str, aes_key: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(
        {"v": 1, "api_key_id": str(api_key_id), "secret": secret},
        separators=(",", ":"),
    ).encode()
    return struct.pack(">H", 1) + nonce + AESGCM(aes_key).encrypt(nonce, plaintext, None)


async def _issue(
    conn: asyncpg.Connection,
    key_id: uuid.UUID,
    secret: str,
    audit_id: uuid.UUID,
    aes_key: bytes,
    *,
    name: str = "Contract key",
    idempotency_digest: str | None = None,
    request_fingerprint: str | None = None,
    permanent_reason: str | None = "Permanent contract credential",
    expires_at: datetime | None = None,
    actor_id: uuid.UUID = ACTOR_ID,
    session_id: uuid.UUID = SESSION_ID,
) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT * FROM issue_api_key($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        key_id,
        TENANT_ID,
        actor_id,
        session_id,
        name,
        secret[:12],
        hashlib.sha256(secret.encode()).hexdigest(),
        "data_reader",
        expires_at,
        idempotency_digest or secrets.token_hex(32),
        request_fingerprint or secrets.token_hex(32),
        _app_escrow(key_id, secret, aes_key),
        permanent_reason,
        audit_id,
    )


async def _rotate(
    conn: asyncpg.Connection,
    old_id: uuid.UUID,
    new_id: uuid.UUID,
    secret: str,
    audit_id: uuid.UUID,
    aes_key: bytes,
    *,
    name: str = "Contract key",
    idempotency_digest: str | None = None,
    request_fingerprint: str | None = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT * FROM rotate_api_key($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        old_id,
        new_id,
        TENANT_ID,
        ACTOR_ID,
        SESSION_ID,
        name,
        secret[:12],
        hashlib.sha256(secret.encode()).hexdigest(),
        "data_reader",
        None,
        idempotency_digest or secrets.token_hex(32),
        request_fingerprint or secrets.token_hex(32),
        _app_escrow(new_id, secret, aes_key),
        audit_id,
    )


@pytest.mark.acceptance
def test_expand_finalize_functions_and_authenticated_escrow_roundtrip() -> None:
    migration_key = secrets.token_urlsafe(48)
    aes_key = secrets.token_bytes(32)
    legacy_secret = "ymt_" + "a" * 48
    old_drain_secret = "ymt_" + "b" * 48
    expand_secret = "ymt_" + "c" * 48
    head_secret = "ymt_" + "d" * 48
    rotated_legacy_secret = "ymt_" + "e" * 48
    far_future_legacy_secret = "ymt_" + "f" * 48
    legacy_id = uuid.UUID("71111111-1111-7111-8111-111111111111")
    far_future_legacy_id = uuid.uuid4()
    old_drain_id, expand_id, head_id, rotated_legacy_id = (uuid.uuid4() for _ in range(4))

    with _owned_database() as database_name:
        _run_alembic(database_name, "upgrade", "adde49f79bcd")

        async def seed_legacy() -> None:
            conn = await asyncpg.connect(_owner_dsn(database_name))
            try:
                await _seed_actor(conn, f"api-key-stage-{secrets.token_hex(4)}")
                await conn.execute(
                    "INSERT INTO api_keys (id,tenant_id,name,key,role,permissions,revoked) "
                    "VALUES ($1,$2,'Legacy',$3,'data_reader',$4::json,false)",
                    legacy_id,
                    TENANT_ID,
                    legacy_secret,
                    json.dumps(API_KEY_ROLE_PERMISSIONS["data_reader"]),
                )
                await conn.execute(
                    "INSERT INTO api_keys (id,tenant_id,name,key,role,permissions,revoked,expires_at) "
                    "VALUES ($1,$2,'Legacy far future',$3,'data_reader',$4::json,false,"
                    "now()+interval '730 days')",
                    far_future_legacy_id,
                    TENANT_ID,
                    far_future_legacy_secret,
                    json.dumps(API_KEY_ROLE_PERMISSIONS["data_reader"]),
                )
            finally:
                await conn.close()

        asyncio.run(seed_legacy())
        _run_alembic(database_name, "upgrade", "b10d4e6f8a20", expect_success=False)

        async def assert_expand_failed_before_mutation() -> None:
            conn = await asyncpg.connect(_owner_dsn(database_name))
            try:
                assert await conn.fetchval("SELECT version_num FROM alembic_version") == "adde49f79bcd"
                assert await conn.fetchval("SELECT to_regclass('public.api_key_legacy_secret_backups')") is None
                assert not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='api_keys' AND column_name='key_digest')"
                )
            finally:
                await conn.close()

        asyncio.run(assert_expand_failed_before_mutation())
        _run_alembic(database_name, "upgrade", "b10d4e6f8a20", migration_key=migration_key)

        async def exercise_expand() -> None:
            owner = await asyncpg.connect(_owner_dsn(database_name))
            runtime = await asyncpg.connect(_runtime_dsn(database_name))
            try:
                await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
                assert {
                    row[0]
                    for row in await owner.fetch(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee='yimatong_app' AND table_name='api_keys'"
                    )
                } == {"SELECT", "INSERT", "UPDATE", "DELETE"}
                assert await owner.fetchval(
                    "SELECT has_function_privilege('yimatong_app',"
                    "'public.api_key_permissions_for_role(text)','EXECUTE')"
                )
                async with runtime.transaction():
                    await _set_tenant(runtime)
                    await runtime.execute(
                        "INSERT INTO api_keys (id,tenant_id,name,key,role,permissions,revoked) "
                        "VALUES ($1,$2,'Old drain',$3,'data_reader',$4::json,false)",
                        old_drain_id,
                        TENANT_ID,
                        old_drain_secret,
                        json.dumps(API_KEY_ROLE_PERMISSIONS["data_reader"]),
                    )
                    assert (
                        await runtime.fetchval("SELECT key_digest FROM api_keys WHERE id=$1", old_drain_id)
                        == hashlib.sha256(old_drain_secret.encode()).hexdigest()
                    )
                    issued = await _issue(runtime, expand_id, expand_secret, uuid.uuid4(), aes_key)
                    assert issued["api_key_id"] == expand_id
                    assert not issued["replayed"]
                    assert bytes(issued["escrow_ciphertext"])
                    assert await runtime.fetchval("SELECT key IS NULL FROM api_keys WHERE id=$1", expand_id)
            finally:
                await runtime.close()
                await owner.close()

        asyncio.run(exercise_expand())
        _run_alembic(database_name, "downgrade", "-1", migration_key=migration_key, expect_success=False)
        _run_alembic(
            database_name,
            "downgrade",
            "-1",
            migration_key=migration_key,
            aes_key=secrets.token_bytes(32),
            expect_success=False,
        )
        _run_alembic(database_name, "downgrade", "-1", migration_key=migration_key, aes_key=aes_key)

        async def assert_expand_downgrade_restored() -> None:
            conn = await asyncpg.connect(_owner_dsn(database_name))
            try:
                assert await conn.fetchval("SELECT version_num FROM alembic_version") == "adde49f79bcd"
                restored = await conn.fetch("SELECT key FROM api_keys ORDER BY key")
                assert [row["key"] for row in restored] == [
                    legacy_secret,
                    old_drain_secret,
                    expand_secret,
                    far_future_legacy_secret,
                ]
                assert not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='api_keys' AND column_name='permanent_reason')"
                )
            finally:
                await conn.close()

        asyncio.run(assert_expand_downgrade_restored())
        _run_alembic(database_name, "upgrade", "b10d4e6f8a20", migration_key=migration_key)
        _run_alembic(
            database_name,
            "upgrade",
            "head",
            migration_key=secrets.token_urlsafe(48),
            expect_success=False,
        )
        _run_alembic(database_name, "upgrade", "head", expect_success=False)
        _run_alembic(database_name, "upgrade", "head", migration_key=migration_key)

        async def exercise_head() -> None:
            owner = await asyncpg.connect(_owner_dsn(database_name))
            runtime = await asyncpg.connect(_runtime_dsn(database_name))
            try:
                await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
                assert not await owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='api_keys' AND column_name='key')"
                )
                assert [
                    row[0]
                    for row in await owner.fetch(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee='yimatong_app' AND table_name='api_keys' ORDER BY privilege_type"
                    )
                ] == ["SELECT"]
                assert not await owner.fetchval(
                    "SELECT has_function_privilege('yimatong_app',"
                    "'public.api_key_permissions_for_role(text)','EXECUTE')"
                )
                async with runtime.transaction():
                    await _set_tenant(runtime)
                    issued = await _issue(runtime, head_id, head_secret, uuid.uuid4(), aes_key)
                    assert issued["api_key_id"] == head_id and not issued["replayed"]
                    assert (
                        await runtime.fetchval("SELECT permanent_reason FROM api_keys WHERE id=$1", legacy_id)
                        == "Legacy credential retained during hashed-key migration"
                    )
                    assert await runtime.fetchval(
                        "SELECT expires_at > CURRENT_TIMESTAMP + interval '365 days' "
                        "FROM api_keys WHERE id=$1 AND idempotency_key_digest IS NULL",
                        far_future_legacy_id,
                    )
                    rotate_audit_id = uuid.uuid4()
                    rotated = await _rotate(
                        runtime,
                        legacy_id,
                        rotated_legacy_id,
                        rotated_legacy_secret,
                        rotate_audit_id,
                        aes_key,
                        name="Legacy",
                    )
                    assert rotated["api_key_id"] == rotated_legacy_id and not rotated["replayed"]
                    assert (
                        await runtime.fetchval("SELECT permanent_reason FROM api_keys WHERE id=$1", rotated_legacy_id)
                        == "Legacy credential retained during hashed-key migration"
                    )
                    audit_details = json.loads(
                        await runtime.fetchval("SELECT details FROM platform_audit_log WHERE id=$1", rotate_audit_id)
                    )
                    assert audit_details["permanent_reason"] == (
                        "Legacy credential retained during hashed-key migration"
                    )
            finally:
                await runtime.close()
                await owner.close()

        asyncio.run(exercise_head())
        _run_alembic(
            database_name,
            "downgrade",
            "-1",
            migration_key=secrets.token_urlsafe(48),
            aes_key=aes_key,
            expect_success=False,
        )
        _run_alembic(
            database_name,
            "downgrade",
            "-1",
            migration_key=migration_key,
            aes_key=secrets.token_bytes(32),
            expect_success=False,
        )
        _run_alembic(database_name, "downgrade", "-1", migration_key=migration_key, expect_success=False)
        _run_alembic(database_name, "downgrade", "-1", migration_key=migration_key, aes_key=aes_key)

        async def assert_restored() -> None:
            conn = await asyncpg.connect(_owner_dsn(database_name))
            try:
                restored = await conn.fetch("SELECT key FROM api_keys ORDER BY key")
                assert [row["key"] for row in restored] == [
                    legacy_secret,
                    old_drain_secret,
                    expand_secret,
                    head_secret,
                    rotated_legacy_secret,
                    far_future_legacy_secret,
                ]
            finally:
                await conn.close()

        asyncio.run(assert_restored())
        _run_alembic(database_name, "downgrade", "-1")


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_head_lifecycle_acl_rls_and_revoke_serialization(migrated_pg_url: str) -> None:
    database_name = migrated_pg_url.rsplit("/", 1)[-1]
    aes_key = secrets.token_bytes(32)
    owner = await asyncpg.connect(_owner_dsn(database_name))
    runtime_a = await asyncpg.connect(_runtime_dsn(database_name))
    runtime_b = await asyncpg.connect(_runtime_dsn(database_name))
    try:
        await _seed_actor(owner, f"api-key-head-{secrets.token_hex(4)}")
        assert (
            json.loads(await owner.fetchval("SELECT api_key_permissions_for_role('data_reader')::text"))
            == (API_KEY_ROLE_PERMISSIONS["data_reader"])
        )
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.api_key_legacy_secret_backups','SELECT')"
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','public.api_key_permissions_for_role(text)','EXECUTE')"
        )
        issue_signature = (
            "public.issue_api_key(uuid,uuid,uuid,uuid,text,text,text,text,timestamp with time zone,"
            "text,text,bytea,text,uuid)"
        )
        rotate_signature = (
            "public.rotate_api_key(uuid,uuid,uuid,uuid,uuid,text,text,text,text,timestamp with time zone,"
            "text,text,bytea,uuid)"
        )
        for signature in (issue_signature, rotate_signature):
            function_oid = await owner.fetchval("SELECT to_regprocedure($1)", signature)
            assert function_oid is not None
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
            assert await owner.fetchval("SELECT pg_get_function_result($1::regprocedure)", signature) == (
                "TABLE(api_key_id uuid, replayed boolean, escrow_ciphertext bytea)"
            )
        assert not await owner.fetchval(
            "SELECT has_table_privilege('public','public.api_key_legacy_secret_backups','SELECT')"
        )

        boundary_id, boundary_audit_id = uuid.uuid4(), uuid.uuid4()
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            exact_boundary = await runtime_a.fetchval("SELECT CURRENT_TIMESTAMP + interval '365 days'")
            boundary = await _issue(
                runtime_a,
                boundary_id,
                "ymt_" + secrets.token_hex(24),
                boundary_audit_id,
                aes_key,
                expires_at=exact_boundary,
                permanent_reason=None,
            )
            assert boundary["api_key_id"] == boundary_id and not boundary["replayed"]

        rejected_expiry_id, rejected_expiry_audit_id = uuid.uuid4(), uuid.uuid4()
        with pytest.raises(
            asyncpg.InvalidParameterValueError, match="expiry acknowledgement is invalid"
        ) as expiry_error:
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                over_boundary = await runtime_a.fetchval("SELECT CURRENT_TIMESTAMP + interval '365 days 1 microsecond'")
                await _issue(
                    runtime_a,
                    rejected_expiry_id,
                    "ymt_" + secrets.token_hex(24),
                    rejected_expiry_audit_id,
                    aes_key,
                    expires_at=over_boundary,
                    permanent_reason=None,
                )
        assert expiry_error.value.sqlstate == "22023"
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM api_keys WHERE id=$1)", rejected_expiry_id)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", rejected_expiry_audit_id
        )

        direct_expiry_id = uuid.uuid4()
        with pytest.raises(asyncpg.InvalidParameterValueError, match="API key metadata is invalid") as direct_error:
            await owner.execute(
                "INSERT INTO api_keys "
                "(id,tenant_id,name,key_prefix,key_digest,role,permissions,revoked,created_by,"
                "expires_at,idempotency_key_digest,request_fingerprint) "
                "VALUES ($1,$2,'Direct expiry bypass','ymt_00000000',$3,'data_reader',$4::json,false,$5,"
                "CURRENT_TIMESTAMP + interval '366 days',$6,$7)",
                direct_expiry_id,
                TENANT_ID,
                secrets.token_hex(32),
                json.dumps(API_KEY_ROLE_PERMISSIONS["data_reader"]),
                ACTOR_ID,
                secrets.token_hex(32),
                secrets.token_hex(32),
            )
        assert direct_error.value.sqlstate == "22023"
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM api_keys WHERE id=$1)", direct_expiry_id)

        old_secret = "ymt_" + secrets.token_hex(24)
        new_secret = "ymt_" + secrets.token_hex(24)
        old_id, new_id = uuid.uuid4(), uuid.uuid4()
        issue_audit_id, rotate_audit_id = uuid.uuid4(), uuid.uuid4()
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            issued = await _issue(runtime_a, old_id, old_secret, issue_audit_id, aes_key)
            assert issued["api_key_id"] == old_id and not issued["replayed"]

        with pytest.raises(asyncpg.SerializationError, match="rotation metadata is stale"):
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                await _rotate(runtime_a, old_id, new_id, new_secret, uuid.uuid4(), aes_key, name="stale")

        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            rotated = await _rotate(runtime_a, old_id, new_id, new_secret, rotate_audit_id, aes_key)
            assert rotated["api_key_id"] == new_id and not rotated["replayed"]

        audit_rows = await owner.fetch(
            "SELECT id,operator_id,action,resource,details FROM platform_audit_log WHERE id=ANY($1::uuid[])",
            [issue_audit_id, rotate_audit_id],
        )
        audits = {row["id"]: row for row in audit_rows}
        assert set(audits) == {issue_audit_id, rotate_audit_id}
        assert audits[issue_audit_id]["operator_id"] == str(ACTOR_ID)
        assert audits[issue_audit_id]["action"] == "api_key_issued"
        assert audits[issue_audit_id]["resource"] == f"api_key:{old_id}"
        assert json.loads(audits[issue_audit_id]["details"]) == {
            "key_prefix": old_secret[:12],
            "role": "data_reader",
            "expires_at": None,
            "permanent_reason": "Permanent contract credential",
        }
        assert audits[rotate_audit_id]["operator_id"] == str(ACTOR_ID)
        assert audits[rotate_audit_id]["action"] == "api_key_rotated"
        assert audits[rotate_audit_id]["resource"] == f"api_key:{new_id}"
        assert json.loads(audits[rotate_audit_id]["details"]) == {
            "key_prefix": new_secret[:12],
            "role": "data_reader",
            "expires_at": None,
            "permanent_reason": "Permanent contract credential",
            "rotated_from_id": str(old_id),
        }
        async with owner.transaction():
            await _set_tenant(owner)
            assert await owner.fetchval("SELECT rotated_from_id FROM api_keys WHERE id=$1", new_id) == old_id

        idem_digest, issue_fingerprint = secrets.token_hex(32), secrets.token_hex(32)
        idem_id, ignored_replay_id = uuid.uuid4(), uuid.uuid4()
        idem_secret, ignored_secret = "ymt_" + secrets.token_hex(24), "ymt_" + secrets.token_hex(24)
        idem_audit_id = uuid.uuid4()
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            first = await _issue(
                runtime_a,
                idem_id,
                idem_secret,
                idem_audit_id,
                aes_key,
                idempotency_digest=idem_digest,
                request_fingerprint=issue_fingerprint,
            )
            replay = await _issue(
                runtime_a,
                ignored_replay_id,
                ignored_secret,
                uuid.uuid4(),
                aes_key,
                idempotency_digest=idem_digest,
                request_fingerprint=issue_fingerprint,
            )
            assert first["api_key_id"] == idem_id and not first["replayed"]
            assert replay["api_key_id"] == idem_id and replay["replayed"]
            assert bytes(replay["escrow_ciphertext"]) == bytes(first["escrow_ciphertext"])
        assert (
            await owner.fetchval("SELECT count(*) FROM api_keys WHERE id=ANY($1::uuid[])", [idem_id, ignored_replay_id])
            == 1
        )
        assert await owner.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", idem_audit_id) == 1
        with pytest.raises(asyncpg.SerializationError, match="idempotency payload conflicts"):
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                await _issue(
                    runtime_a,
                    uuid.uuid4(),
                    "ymt_" + secrets.token_hex(24),
                    uuid.uuid4(),
                    aes_key,
                    idempotency_digest=idem_digest,
                    request_fingerprint=secrets.token_hex(32),
                )

        rotate_idem, rotate_fingerprint = secrets.token_hex(32), secrets.token_hex(32)
        idem_rotated_id, ignored_rotate_id = uuid.uuid4(), uuid.uuid4()
        idem_rotated_secret = "ymt_" + secrets.token_hex(24)
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            first_rotate = await _rotate(
                runtime_a,
                idem_id,
                idem_rotated_id,
                idem_rotated_secret,
                uuid.uuid4(),
                aes_key,
                idempotency_digest=rotate_idem,
                request_fingerprint=rotate_fingerprint,
            )
            replay_rotate = await _rotate(
                runtime_a,
                idem_id,
                ignored_rotate_id,
                "ymt_" + secrets.token_hex(24),
                uuid.uuid4(),
                aes_key,
                idempotency_digest=rotate_idem,
                request_fingerprint=rotate_fingerprint,
            )
            assert first_rotate["api_key_id"] == idem_rotated_id and not first_rotate["replayed"]
            assert replay_rotate["api_key_id"] == idem_rotated_id and replay_rotate["replayed"]
            assert bytes(replay_rotate["escrow_ciphertext"]) == bytes(first_rotate["escrow_ciphertext"])
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM api_keys WHERE id=$1)", ignored_rotate_id)

        with pytest.raises(asyncpg.InvalidParameterValueError, match="API key metadata is invalid") as bypass_error:
            await owner.execute(
                "INSERT INTO api_keys "
                "(id,tenant_id,name,key_prefix,key_digest,role,permissions,revoked,created_by) "
                "VALUES ($1,$2,'Bypass','ymt_00000000',$3,'data_reader',$4::json,false,$5)",
                uuid.uuid4(),
                TENANT_ID,
                secrets.token_hex(32),
                json.dumps(API_KEY_ROLE_PERMISSIONS["data_reader"]),
                ACTOR_ID,
            )
        assert bypass_error.value.sqlstate == "22023"

        non_admin_actor, non_admin_session, non_admin_role = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        tenant_manage_permission = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='tenant:manage'", TENANT_ID
        )
        await owner.execute(
            "INSERT INTO accounts "
            "(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts) "
            "VALUES ($1,$2,$3,$4,'unused','Non-admin manager',true,0,false,0)",
            non_admin_actor,
            TENANT_ID,
            uuid.UUID("21111111-1111-7111-8111-111111111111"),
            f"non-admin-{non_admin_actor}@example.invalid",
        )
        await owner.execute(
            "INSERT INTO roles (id,tenant_id,name) VALUES ($1,$2,'api_manager')",
            non_admin_role,
            TENANT_ID,
        )
        await owner.execute(
            "INSERT INTO account_roles (tenant_id,account_id,role_id) VALUES ($1,$2,$3)",
            TENANT_ID,
            non_admin_actor,
            non_admin_role,
        )
        await owner.execute(
            "INSERT INTO role_permissions (tenant_id,role_id,permission_id) VALUES ($1,$2,$3)",
            TENANT_ID,
            non_admin_role,
            tenant_manage_permission,
        )
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at) "
            "VALUES ($1,$2,$3,0,$4,now()+interval '1 day')",
            non_admin_session,
            non_admin_actor,
            TENANT_ID,
            f"non-admin-{uuid.uuid4()}",
        )
        rejected_key_id, rejected_audit_id = uuid.uuid4(), uuid.uuid4()
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="not a tenant administrator") as non_admin_error:
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                await _issue(
                    runtime_a,
                    rejected_key_id,
                    "ymt_" + secrets.token_hex(24),
                    rejected_audit_id,
                    aes_key,
                    actor_id=non_admin_actor,
                    session_id=non_admin_session,
                )
        assert non_admin_error.value.sqlstate == "42501"
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM api_keys WHERE id=$1)", rejected_key_id)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", rejected_audit_id
        )

        assert (
            await runtime_a.fetchval(
                "SELECT count(*) FROM resolve_active_api_key($1)", hashlib.sha256(old_secret.encode()).hexdigest()
            )
            == 0
        )
        assert (
            await runtime_a.fetchval(
                "SELECT count(*) FROM resolve_active_api_key($1)", hashlib.sha256(new_secret.encode()).hexdigest()
            )
            == 1
        )

        concurrent_secret = "ymt_" + secrets.token_hex(24)
        concurrent_id = uuid.uuid4()
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            concurrent = await _issue(runtime_a, concurrent_id, concurrent_secret, uuid.uuid4(), aes_key)
            assert concurrent["api_key_id"] == concurrent_id

        tx_a = runtime_a.transaction()
        await tx_a.start()
        await _set_tenant(runtime_a)
        assert await runtime_a.fetchval("SELECT lock_active_api_key($1,$2)", TENANT_ID, concurrent_id)

        async def revoke() -> uuid.UUID | None:
            async with runtime_b.transaction():
                await _set_tenant(runtime_b)
                return await runtime_b.fetchval(
                    "SELECT revoke_api_key($1,$2,$3,$4,$5)",
                    concurrent_id,
                    TENANT_ID,
                    ACTOR_ID,
                    SESSION_ID,
                    uuid.uuid4(),
                )

        revoke_task = asyncio.create_task(revoke())
        await asyncio.sleep(0.15)
        assert not revoke_task.done()
        await tx_a.commit()
        assert await revoke_task == concurrent_id
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            assert not await runtime_a.fetchval("SELECT lock_active_api_key($1,$2)", TENANT_ID, concurrent_id)

        active_count = await owner.fetchval(
            "SELECT count(*) FROM api_keys WHERE tenant_id=$1 AND NOT revoked "
            "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())",
            TENANT_ID,
        )
        for index in range(active_count, 20):
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                capped = await _issue(
                    runtime_a,
                    uuid.uuid4(),
                    "ymt_" + secrets.token_hex(24),
                    uuid.uuid4(),
                    aes_key,
                    name=f"Cap credential {index}",
                )
                assert not capped["replayed"]
        with pytest.raises(asyncpg.ProgramLimitExceededError, match="active limit reached"):
            async with runtime_a.transaction():
                await _set_tenant(runtime_a)
                await _issue(
                    runtime_a,
                    uuid.uuid4(),
                    "ymt_" + secrets.token_hex(24),
                    uuid.uuid4(),
                    aes_key,
                    name="Cap overflow",
                )

        await owner.execute("UPDATE tenants SET plan_expires_at=now()-interval '1 minute' WHERE id=$1", TENANT_ID)
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="plan is expired"):
                await _issue(runtime_a, uuid.uuid4(), "ymt_" + secrets.token_hex(24), uuid.uuid4(), aes_key)
        revoke_audit_id = uuid.uuid4()
        async with runtime_a.transaction():
            await _set_tenant(runtime_a)
            assert (
                await runtime_a.fetchval(
                    "SELECT revoke_api_key($1,$2,$3,$4,$5)",
                    new_id,
                    TENANT_ID,
                    ACTOR_ID,
                    SESSION_ID,
                    revoke_audit_id,
                )
                == new_id
            )

        revoke_audit = await owner.fetchrow(
            "SELECT operator_id,action,resource,details FROM platform_audit_log WHERE id=$1",
            revoke_audit_id,
        )
        assert revoke_audit is not None
        assert revoke_audit["operator_id"] == str(ACTOR_ID)
        assert revoke_audit["action"] == "api_key_revoked"
        assert revoke_audit["resource"] == f"api_key:{new_id}"
        assert json.loads(revoke_audit["details"]) == {
            "key_prefix": new_secret[:12],
            "role": "data_reader",
            "expires_at": None,
            "permanent_reason": "Permanent contract credential",
        }

        assert not await owner.fetchval("SELECT has_parameter_privilege('yimatong_app','app.bypass_rls','SET')")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_a.execute("UPDATE api_keys SET name='forbidden' WHERE id=$1", new_id)
    finally:
        await runtime_b.close()
        await runtime_a.close()
        await owner.close()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_http_lifecycle_secret_visibility_and_recovery_boundaries(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_name = migrated_pg_url.rsplit("/", 1)[-1]
    owner = await asyncpg.connect(_owner_dsn(database_name))
    runtime_engine = create_async_engine(_runtime_sqlalchemy_url(migrated_pg_url))
    control_engine = create_async_engine(_control_sqlalchemy_url(migrated_pg_url))
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    previous_overrides = app.dependency_overrides.copy()
    principal = await _seed_http_principal(owner, f"api-key-http-{secrets.token_hex(4)}")
    admin_headers = _admin_headers(principal)

    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", control_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    monkeypatch.setenv("AES_MASTER_KEY_V1", secrets.token_hex(32))

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            issued = await client.post(
                "/api/v1/webhooks/api-keys",
                headers=_lifecycle_headers(admin_headers),
                json={
                    "name": "HTTP lifecycle",
                    "role": "data_reader",
                    "permanent_acknowledged": True,
                    "permanent_reason": "HTTP lifecycle contract credential",
                },
            )
            assert issued.status_code == 201, issued.text
            issued_body = issued.json()
            issued_id = uuid.UUID(issued_body["id"])
            issued_secret = issued_body["key"]
            assert issued_secret.startswith("ymt_") and len(issued_secret) == 52
            assert issued_body["key_prefix"] == issued_secret[:12]
            assert "key_digest" not in issued_body
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": issued_secret})).status_code == 200

            listed = await client.get("/api/v1/webhooks/api-keys", headers=admin_headers)
            assert listed.status_code == 200, listed.text
            listed_body = listed.json()
            assert any(row["id"] == str(issued_id) for row in listed_body["items"])
            assert all("key" not in row and "key_digest" not in row for row in listed_body["items"])

            rotated = await client.post(
                f"/api/v1/webhooks/api-keys/{issued_id}/rotate",
                headers=_lifecycle_headers(admin_headers),
            )
            assert rotated.status_code == 201, rotated.text
            rotated_body = rotated.json()
            rotated_id = uuid.UUID(rotated_body["id"])
            rotated_secret = rotated_body["key"]
            assert rotated_secret.startswith("ymt_") and len(rotated_secret) == 52
            assert rotated_body["key_prefix"] == rotated_secret[:12]
            assert "key_digest" not in rotated_body
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": issued_secret})).status_code == 401
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": rotated_secret})).status_code == 200

            revoked = await client.delete(
                f"/api/v1/webhooks/api-keys/{rotated_id}",
                headers=admin_headers,
            )
            assert revoked.status_code == 200, revoked.text
            assert revoked.json() == {"revoked": True}
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": rotated_secret})).status_code == 401

            state_key = await client.post(
                "/api/v1/webhooks/api-keys",
                headers=_lifecycle_headers(admin_headers),
                json={
                    "name": "Live state",
                    "role": "data_reader",
                    "permanent_acknowledged": True,
                    "permanent_reason": "Live tenant state contract credential",
                },
            )
            assert state_key.status_code == 201, state_key.text
            state_secret = state_key.json()["key"]
            await owner.execute("UPDATE tenants SET status='suspended' WHERE id=$1", principal.tenant_id)
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": state_secret})).status_code == 401
            await owner.execute(
                "UPDATE tenants SET status='active',tenant_type='agency' WHERE id=$1", principal.tenant_id
            )
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": state_secret})).status_code == 401
            await owner.execute("UPDATE tenants SET tenant_type='brand' WHERE id=$1", principal.tenant_id)
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": state_secret})).status_code == 200

            recovery_key = await client.post(
                "/api/v1/webhooks/api-keys",
                headers=_lifecycle_headers(admin_headers),
                json={
                    "name": "Expired-plan recovery",
                    "role": "data_reader",
                    "permanent_acknowledged": True,
                    "permanent_reason": "Expired plan recovery credential",
                },
            )
            assert recovery_key.status_code == 201, recovery_key.text
            recovery_body = recovery_key.json()
            recovery_id = uuid.UUID(recovery_body["id"])
            recovery_secret = recovery_body["key"]
            await owner.execute(
                "UPDATE tenants SET plan_expires_at=now()-interval '1 minute' WHERE id=$1",
                principal.tenant_id,
            )
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": recovery_secret})).status_code == 200
            assert (await client.get("/api/v1/webhooks/api-keys", headers=admin_headers)).status_code == 200

            blocked_issue = await client.post(
                "/api/v1/webhooks/api-keys",
                headers=_lifecycle_headers(admin_headers),
                json={
                    "name": "Blocked issue",
                    "role": "data_reader",
                    "permanent_acknowledged": True,
                    "permanent_reason": "Blocked issue contract credential",
                },
            )
            assert blocked_issue.status_code == 403
            assert blocked_issue.json()["code"] == "TENANT_PLAN_EXPIRED"
            blocked_rotate = await client.post(
                f"/api/v1/webhooks/api-keys/{recovery_id}/rotate",
                headers=_lifecycle_headers(admin_headers),
            )
            assert blocked_rotate.status_code == 403
            assert blocked_rotate.json()["code"] == "TENANT_PLAN_EXPIRED"

            recovered = await client.delete(
                f"/api/v1/webhooks/api-keys/{recovery_id}",
                headers=admin_headers,
            )
            assert recovered.status_code == 200, recovered.text
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": recovery_secret})).status_code == 401

        runtime = await asyncpg.connect(_runtime_dsn(database_name))
        try:
            direct_write_statements = (
                (
                    "INSERT INTO api_keys "
                    "(id,tenant_id,name,key_prefix,key_digest,role,permissions,revoked) "
                    "VALUES ($1,$2,'forbidden','ymt_00000000',$3,'data_reader','[]'::json,false)",
                    (uuid.uuid4(), principal.tenant_id, "0" * 64),
                ),
                ("UPDATE api_keys SET name='forbidden' WHERE tenant_id=$1", (principal.tenant_id,)),
                ("DELETE FROM api_keys WHERE tenant_id=$1", (principal.tenant_id,)),
            )
            for statement, parameters in direct_write_statements:
                transaction = runtime.transaction()
                await transaction.start()
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(principal.tenant_id))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(statement, *parameters)
                await transaction.rollback()
        finally:
            await runtime.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner.close()


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_http_mutation_credential_transition_serialization_and_audit_rollback(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import webhooks as webhook_routes

    database_name = migrated_pg_url.rsplit("/", 1)[-1]
    owner = await asyncpg.connect(_owner_dsn(database_name))
    runtime_engine = create_async_engine(_runtime_sqlalchemy_url(migrated_pg_url))
    control_engine = create_async_engine(_control_sqlalchemy_url(migrated_pg_url))
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    previous_overrides = app.dependency_overrides.copy()
    principal = await _seed_http_principal(owner, f"api-key-race-{secrets.token_hex(4)}")
    admin_headers = _admin_headers(principal)
    original_revalidate = database._revalidate_api_key_mutation
    original_revoke = webhook_routes.revoke_api_key
    original_rotate = webhook_routes.rotate_api_key

    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", control_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    monkeypatch.setenv("AES_MASTER_KEY_V1", secrets.token_hex(32))

    async def issue_key(client: AsyncClient, label: str) -> tuple[uuid.UUID, str]:
        response = await client.post(
            "/api/v1/webhooks/api-keys",
            headers=_lifecycle_headers(admin_headers),
            json={
                "name": label,
                "role": "coupon_operator",
                "permanent_acknowledged": True,
                "permanent_reason": f"{label} contract credential",
            },
        )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"]), response.json()["key"]

    async def mutate(client: AsyncClient, claim_id: uuid.UUID, secret: str, *, pause: bool = False):
        headers = {"X-Api-Key": secret}
        if pause:
            headers["X-Test-Pause-After-Key-Lock"] = "1"
        return await client.post(f"/open/v1/coupons/{claim_id}/redeem", headers=headers)

    async def transition(client: AsyncClient, operation: str, key_id: uuid.UUID):
        if operation == "rotate":
            return await client.post(
                f"/api/v1/webhooks/api-keys/{key_id}/rotate",
                headers=_lifecycle_headers(admin_headers),
            )
        return await client.delete(f"/api/v1/webhooks/api-keys/{key_id}", headers=admin_headers)

    async def assert_business_result(claim_id: uuid.UUID, *, changed: bool, audit_count: int) -> None:
        assert await owner.fetchval("SELECT status FROM benefit_claims WHERE id=$1", claim_id) == (
            "used" if changed else "success"
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log "
                "WHERE target_tenant_id=$1 AND action='coupon_redeemed' AND resource=$2",
                str(principal.tenant_id),
                f"claim:{claim_id}",
            )
            == audit_count
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            legacy_key_id, legacy_secret = await issue_key(client, "legacy campaign status")
            legacy_campaign_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO campaigns "
                "(id,tenant_id,name,campaign_type,status,start_at,end_at,rules_json) "
                "VALUES($1,$2,$3,'coupon','draft',now()-interval '1 day',now()+interval '1 day','{}'::json)",
                legacy_campaign_id,
                principal.tenant_id,
                f"Legacy API-key campaign {legacy_campaign_id}",
            )
            current_key_status = await client.patch(
                f"/open/v1/campaigns/{legacy_campaign_id}/status",
                headers={"X-Api-Key": legacy_secret},
                json={"status": "active"},
            )
            assert current_key_status.status_code == 403, current_key_status.text
            await owner.execute("ALTER TABLE api_keys DROP CONSTRAINT ck_api_keys_role_permissions")
            try:
                async with owner.transaction():
                    # Simulate a row persisted before the current metadata guard;
                    # the runtime path below must still reject this legacy scope.
                    await owner.execute("ALTER TABLE api_keys DISABLE TRIGGER USER")
                    await owner.execute(
                        "UPDATE api_keys SET permissions='[\"campaign:status\"]'::json WHERE tenant_id=$1 AND id=$2",
                        principal.tenant_id,
                        legacy_key_id,
                    )
                    await owner.execute("ALTER TABLE api_keys ENABLE TRIGGER USER")
                forbidden_campaign_status = await client.patch(
                    f"/open/v1/campaigns/{legacy_campaign_id}/status",
                    headers={"X-Api-Key": legacy_secret},
                    json={"status": "active"},
                )
                assert forbidden_campaign_status.status_code == 401, forbidden_campaign_status.text
                assert (
                    await owner.fetchval(
                        "SELECT status FROM campaigns WHERE tenant_id=$1 AND id=$2",
                        principal.tenant_id,
                        legacy_campaign_id,
                    )
                    == "draft"
                )
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                        "AND resource=$2 AND action='campaign_status_changed'",
                        str(principal.tenant_id),
                        f"campaign:{legacy_campaign_id}",
                    )
                    == 0
                )
            finally:
                async with owner.transaction():
                    await owner.execute(
                        "DELETE FROM api_keys WHERE tenant_id=$1 AND id=$2",
                        principal.tenant_id,
                        legacy_key_id,
                    )
                    await owner.execute(
                        "ALTER TABLE api_keys ADD CONSTRAINT ck_api_keys_role_permissions "
                        "CHECK (permissions::jsonb = public.api_key_permissions_for_role(role)::jsonb)"
                    )

            for operation in ("revoke", "rotate"):
                key_id, secret = await issue_key(client, f"{operation} change-first")
                claim_id = await _seed_coupon_claim(owner, principal.tenant_id)
                transition_applied = asyncio.Event()
                allow_transition_commit = asyncio.Event()

                if operation == "revoke":

                    async def held_revoke(*args, **kwargs):
                        result = await original_revoke(*args, **kwargs)
                        transition_applied.set()
                        await allow_transition_commit.wait()
                        return result

                    monkeypatch.setattr(webhook_routes, "revoke_api_key", held_revoke)
                else:

                    async def held_rotate(*args, **kwargs):
                        result = await original_rotate(*args, **kwargs)
                        transition_applied.set()
                        await allow_transition_commit.wait()
                        return result

                    monkeypatch.setattr(webhook_routes, "rotate_api_key", held_rotate)

                change_task = asyncio.create_task(transition(client, operation, key_id))
                mutation_task = None
                try:
                    await asyncio.wait_for(transition_applied.wait(), timeout=10)
                    mutation_task = asyncio.create_task(mutate(client, claim_id, secret))
                    await asyncio.sleep(0.15)
                    assert not mutation_task.done(), "credential resolution must wait for the lifecycle transition"
                    allow_transition_commit.set()
                    changed_response = await asyncio.wait_for(change_task, timeout=10)
                    assert changed_response.status_code == (201 if operation == "rotate" else 200), (
                        changed_response.text
                    )
                    mutation_response = await asyncio.wait_for(mutation_task, timeout=10)
                    assert mutation_response.status_code == 401, mutation_response.text
                finally:
                    allow_transition_commit.set()
                    for task in (change_task, mutation_task):
                        if task is not None and not task.done():
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
                    monkeypatch.setattr(webhook_routes, "revoke_api_key", original_revoke)
                    monkeypatch.setattr(webhook_routes, "rotate_api_key", original_rotate)

                await assert_business_result(claim_id, changed=False, audit_count=0)
                assert (await client.get("/open/v1/scans", headers={"X-Api-Key": secret})).status_code == 401
                if operation == "rotate":
                    assert (
                        await client.get("/open/v1/scans", headers={"X-Api-Key": changed_response.json()["key"]})
                    ).status_code == 200

            for operation in ("revoke", "rotate"):
                key_id, secret = await issue_key(client, f"{operation} request-first")
                claim_id = await _seed_coupon_claim(owner, principal.tenant_id)
                request_locked = asyncio.Event()
                allow_request = asyncio.Event()

                async def held_revalidate(session, request, tenant_id):
                    await original_revalidate(session, request, tenant_id)
                    if request.headers.get("X-Test-Pause-After-Key-Lock") == "1":
                        request_locked.set()
                        await allow_request.wait()

                monkeypatch.setattr(database, "_revalidate_api_key_mutation", held_revalidate)
                mutation_task = asyncio.create_task(mutate(client, claim_id, secret, pause=True))
                change_task = None
                try:
                    await asyncio.wait_for(request_locked.wait(), timeout=10)
                    change_task = asyncio.create_task(transition(client, operation, key_id))
                    await asyncio.sleep(0.15)
                    assert not change_task.done(), "lifecycle transition must wait for the in-flight mutation"
                    allow_request.set()
                    mutation_response = await asyncio.wait_for(mutation_task, timeout=10)
                    assert mutation_response.status_code == 200, mutation_response.text
                    changed_response = await asyncio.wait_for(change_task, timeout=10)
                    assert changed_response.status_code == (201 if operation == "rotate" else 200), (
                        changed_response.text
                    )
                finally:
                    allow_request.set()
                    for task in (mutation_task, change_task):
                        if task is not None and not task.done():
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
                    monkeypatch.setattr(database, "_revalidate_api_key_mutation", original_revalidate)

                await assert_business_result(claim_id, changed=True, audit_count=1)
                assert (await client.get("/open/v1/scans", headers={"X-Api-Key": secret})).status_code == 401
                if operation == "rotate":
                    assert (
                        await client.get("/open/v1/scans", headers={"X-Api-Key": changed_response.json()["key"]})
                    ).status_code == 200

            failure_key_id, failure_secret = await issue_key(client, "Audit failure")
            failure_claim_id = await _seed_coupon_claim(owner, principal.tenant_id)
            await owner.execute(
                """
                CREATE OR REPLACE FUNCTION public.acceptance_reject_coupon_audit()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.action = 'coupon_redeemed' THEN
                        RAISE EXCEPTION 'forced coupon audit failure';
                    END IF;
                    RETURN NEW;
                END
                $$
                """
            )
            await owner.execute(
                "CREATE TRIGGER trg_acceptance_reject_coupon_audit "
                "BEFORE INSERT ON platform_audit_log "
                "FOR EACH ROW EXECUTE FUNCTION public.acceptance_reject_coupon_audit()"
            )
            try:
                failed = await mutate(client, failure_claim_id, failure_secret)
                assert failed.status_code == 500, failed.text
            finally:
                await owner.execute("DROP TRIGGER IF EXISTS trg_acceptance_reject_coupon_audit ON platform_audit_log")
                await owner.execute("DROP FUNCTION IF EXISTS public.acceptance_reject_coupon_audit()")
            await assert_business_result(failure_claim_id, changed=False, audit_count=0)
            assert (
                await owner.fetchval("SELECT count(*) FROM api_keys WHERE id=$1 AND revoked=false", failure_key_id) == 1
            )
    finally:
        monkeypatch.setattr(database, "_revalidate_api_key_mutation", original_revalidate)
        monkeypatch.setattr(webhook_routes, "revoke_api_key", original_revoke)
        monkeypatch.setattr(webhook_routes, "rotate_api_key", original_rotate)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await control_engine.dispose()
        await runtime_engine.dispose()
        await owner.close()
