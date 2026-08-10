from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.acceptance

BACKEND_DIR = Path(__file__).resolve().parents[2]
PARENT_REVISION = "d076027f6161"
FENCE_REVISION = "647cdfd9457f"
ONLINE_REVISION = "648cdfd94580"
FINAL_REVISION = "649cdfd94581"
COORDINATOR_KEY = "yimatong.governance_integrity.migration"
QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 0x594D5451554F5441


def _owner_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(database_url: str) -> str:
    return _owner_dsn(database_url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _control_dsn(database_url: str) -> str:
    return _owner_dsn(database_url).replace("yimatong:yimatong@", "acceptance_control:control_pwd@")


def _alembic(
    database_url: str, command: str, target: str, *, succeeds: bool = True
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    result = subprocess.run(
        ["uv", "run", "alembic", command, target],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def _alembic_check(database_url: str) -> None:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    result = subprocess.run(
        ["uv", "run", "alembic", "-x", "baseline_legacy_timestamp_nullability=true", "check"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def _start_alembic(database_url: str, command: str, target: str) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    return await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "alembic",
        command,
        target,
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _stop_alembic_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None:
        return
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.communicate()
    else:
        await process.wait()


async def _replay_runtime_role_acl(database_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(database_url))
    try:
        await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
    finally:
        await owner.close()


async def _drop_backfill_pause_objects(database_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(database_url))
    try:
        await owner.execute("DROP TRIGGER IF EXISTS zz_test_pause_backfill ON account_roles")
        await owner.execute("DROP FUNCTION IF EXISTS public.zz_test_pause_backfill()")
    finally:
        await owner.close()


async def _seed_tenant(conn: asyncpg.Connection, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        tenant_id,
        label,
        f"{label}-{tenant_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        organization_id,
        tenant_id,
        f"{label}-org",
    )
    return tenant_id, organization_id


async def _seed_account(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID,
    label: str,
) -> uuid.UUID:
    account_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        account_id,
        tenant_id,
        organization_id,
        f"{label}-{account_id.hex[:8]}@test.local",
        label,
    )
    return account_id


async def _seed_role(conn: asyncpg.Connection, tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    role_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        role_id,
        tenant_id,
        name,
    )
    return role_id


async def _assert_rejected(conn: asyncpg.Connection, sql: str, *args) -> Exception:
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except Exception as exc:
        return exc
    raise AssertionError("statement unexpectedly succeeded")


async def _index_fingerprint(conn: asyncpg.Connection, index_name: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT table_class.relname AS table_name,index_row.indisvalid,index_row.indisready,"
        "index_row.indislive,index_row.indisunique,index_row.indisprimary,index_row.indisexclusion,"
        "index_row.indimmediate,index_row.indnullsnotdistinct,index_row.indnkeyatts AS key_attribute_count,"
        "index_row.indnatts AS total_attribute_count,access_method.amname AS access_method,"
        "index_row.indpred IS NULL AS has_no_predicate,index_row.indexprs IS NULL AS has_no_expressions,"
        "index_row.indnatts=index_row.indnkeyatts AS has_no_includes,NOT EXISTS (SELECT 1 FROM "
        "unnest(index_row.indclass::oid[]) WITH ORDINALITY AS classes(opclass_oid,ordinality) "
        "JOIN pg_opclass AS opclass ON opclass.oid=classes.opclass_oid "
        "WHERE classes.ordinality<=index_row.indnkeyatts AND NOT opclass.opcdefault) AS uses_default_opclasses,"
        "ARRAY(SELECT attribute.attname FROM unnest(index_row.indkey::smallint[]) WITH ORDINALITY "
        "AS keys(attnum,ordinality) JOIN pg_attribute AS attribute "
        "ON attribute.attrelid=index_row.indrelid AND attribute.attnum=keys.attnum "
        "WHERE keys.ordinality<=index_row.indnkeyatts ORDER BY keys.ordinality) AS key_columns,"
        "ARRAY(SELECT option FROM unnest(index_row.indoption::smallint[]) WITH ORDINALITY "
        "AS options(option,ordinality) WHERE options.ordinality<=index_row.indnkeyatts "
        "ORDER BY options.ordinality) AS key_options,ARRAY(SELECT collation_oid FROM "
        "unnest(index_row.indcollation::oid[]) WITH ORDINALITY AS collations(collation_oid,ordinality) "
        "WHERE collations.ordinality<=index_row.indnkeyatts ORDER BY collations.ordinality) AS key_collations,"
        "ARRAY(SELECT attribute.attcollation FROM unnest(index_row.indkey::smallint[]) WITH ORDINALITY "
        "AS keys(attnum,ordinality) JOIN pg_attribute AS attribute "
        "ON attribute.attrelid=index_row.indrelid AND attribute.attnum=keys.attnum "
        "WHERE keys.ordinality<=index_row.indnkeyatts ORDER BY keys.ordinality) AS base_key_collations "
        "FROM pg_class AS index_class "
        "JOIN pg_namespace AS namespace ON namespace.oid=index_class.relnamespace "
        "JOIN pg_index AS index_row ON index_row.indexrelid=index_class.oid "
        "JOIN pg_class AS table_class ON table_class.oid=index_row.indrelid "
        "JOIN pg_am AS access_method ON access_method.oid=index_class.relam "
        "WHERE namespace.nspname='public' AND index_class.relname=$1",
        index_name,
    )


async def _constraint_fingerprint(
    conn: asyncpg.Connection,
    table_name: str,
    constraint_name: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT constraint_row.contype::text AS contype,constraint_row.convalidated,"
        "constraint_row.condeferrable,constraint_row.condeferred,"
        "constraint_row.confmatchtype::text AS confmatchtype,"
        "constraint_row.confupdtype::text AS confupdtype,"
        "constraint_row.confdeltype::text AS confdeltype,"
        "pg_get_expr(constraint_row.conbin,constraint_row.conrelid,false) AS check_expression,"
        "ARRAY(SELECT attribute.attname FROM unnest(constraint_row.conkey) WITH ORDINALITY "
        "AS keys(attnum,ordinality) JOIN pg_attribute AS attribute "
        "ON attribute.attrelid=constraint_row.conrelid AND attribute.attnum=keys.attnum "
        "ORDER BY keys.ordinality) AS columns FROM pg_constraint AS constraint_row "
        "JOIN pg_class AS owner ON owner.oid=constraint_row.conrelid "
        "JOIN pg_namespace AS namespace ON namespace.oid=owner.relnamespace "
        "WHERE namespace.nspname='public' AND owner.relname=$1 AND constraint_row.conname=$2",
        table_name,
        constraint_name,
    )


async def test_catalog_tenant_links_uniqueness_and_scoped_session_revoker(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    control = await asyncpg.connect(_control_dsn(migrated_pg_url))
    try:
        async with owner.transaction():
            tenant_a, org_a = await _seed_tenant(owner, "integrity-a")
            tenant_b, org_b = await _seed_tenant(owner, "integrity-b")
            account_a = await _seed_account(owner, tenant_a, org_a, "account-a")
            account_b = await _seed_account(owner, tenant_b, org_b, "account-b")
            role_a = await _seed_role(owner, tenant_a, "admin")
            role_b = await _seed_role(owner, tenant_b, "admin")
            role_operator_a = await _seed_role(owner, tenant_a, "operator")
            permission_a = uuid.uuid4()
            permission_b = uuid.uuid4()
            await owner.executemany(
                "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
                [
                    (permission_a, tenant_a, "account:manage"),
                    (permission_b, tenant_b, "account:manage"),
                ],
            )
            await owner.execute("INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)", account_a, role_a)
            await owner.execute("INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)", account_b, role_b)
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_a))
        await runtime.execute("SELECT set_config('app.bypass_rls','false',false)")
        await control.execute("SELECT set_config('app.tenant_id','',false)")
        await control.execute("SELECT set_config('app.bypass_rls','true',false)")

        await runtime.execute("INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)", account_a, role_operator_a)
        await runtime.execute(
            "INSERT INTO role_permissions(role_id,permission_id) VALUES($1,$2)", role_operator_a, permission_a
        )
        assert (
            await owner.fetchval(
                "SELECT tenant_id FROM account_roles WHERE account_id=$1 AND role_id=$2", account_a, role_operator_a
            )
            == tenant_a
        )
        assert isinstance(
            await _assert_rejected(
                control,
                "UPDATE accounts SET tenant_id=$1,organization_id=$2 WHERE id=$3",
                tenant_b,
                org_b,
                account_a,
            ),
            asyncpg.CheckViolationError,
        )
        assert isinstance(
            await _assert_rejected(control, "UPDATE roles SET tenant_id=$1 WHERE id=$2", tenant_b, role_a),
            asyncpg.CheckViolationError,
        )
        assert isinstance(
            await _assert_rejected(
                control,
                "UPDATE account_roles SET tenant_id=$1,account_id=$2,role_id=$3 "
                "WHERE tenant_id=$4 AND account_id=$5 AND role_id=$6",
                tenant_b,
                account_b,
                role_b,
                tenant_a,
                account_a,
                role_operator_a,
            ),
            asyncpg.CheckViolationError,
        )
        assert isinstance(
            await _assert_rejected(runtime, "UPDATE roles SET name='former-admin' WHERE id=$1", role_a),
            asyncpg.CheckViolationError,
        )
        tenant_without_accounts, org_without_accounts = await _seed_tenant(owner, "identity-only")
        assert isinstance(
            await _assert_rejected(
                owner,
                "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
                "is_active,auth_version,must_change_password,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'x','no-admin',0,true,0,false,now(),now())",
                uuid.uuid4(),
                tenant_without_accounts,
                org_without_accounts,
                f"no-admin-{uuid.uuid4().hex[:8]}@test.local",
            ),
            asyncpg.CheckViolationError,
        )
        assert (
            await owner.fetchval(
                "SELECT tenant_id FROM role_permissions WHERE role_id=$1 AND permission_id=$2",
                role_operator_a,
                permission_a,
            )
            == tenant_a
        )

        cross_account = uuid.uuid4()
        rejected = await _assert_rejected(
            runtime,
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
            "is_active,auth_version,must_change_password,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'x','cross',0,true,0,false,now(),now())",
            cross_account,
            tenant_a,
            org_b,
            f"cross-{cross_account.hex[:8]}@test.local",
        )
        assert isinstance(rejected, asyncpg.ForeignKeyViolationError)
        assert isinstance(
            await _assert_rejected(
                control, "INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)", account_a, role_b
            ),
            asyncpg.ForeignKeyViolationError,
        )
        assert isinstance(
            await _assert_rejected(
                control,
                "INSERT INTO role_permissions(role_id,permission_id) VALUES($1,$2)",
                role_a,
                permission_b,
            ),
            asyncpg.ForeignKeyViolationError,
        )
        assert isinstance(
            await _assert_rejected(
                runtime,
                "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
                uuid.uuid4(),
                tenant_a,
            ),
            asyncpg.UniqueViolationError,
        )
        assert isinstance(
            await _assert_rejected(
                runtime,
                "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) "
                "VALUES($1,$2,'account:manage',now(),now())",
                uuid.uuid4(),
                tenant_a,
            ),
            asyncpg.UniqueViolationError,
        )

        own_session, foreign_session = uuid.uuid4(), uuid.uuid4()
        await owner.executemany(
            "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 day')",
            [
                (own_session, account_a, tenant_a, uuid.uuid4().hex),
                (foreign_session, account_b, tenant_b, uuid.uuid4().hex),
            ],
        )
        assert await runtime.fetchval("SELECT revoke_current_tenant_account_sessions($1)", account_a) == 1
        assert await owner.fetchval("SELECT revoked_at IS NOT NULL FROM auth_sessions WHERE id=$1", own_session)
        assert not await owner.fetchval("SELECT revoked_at IS NOT NULL FROM auth_sessions WHERE id=$1", foreign_session)
        assert isinstance(
            await _assert_rejected(runtime, "SELECT revoke_current_tenant_account_sessions($1)", account_b),
            asyncpg.InsufficientPrivilegeError,
        )
        await runtime.execute("SELECT set_config('app.tenant_id','platform',false)")
        assert isinstance(
            await _assert_rejected(runtime, "SELECT revoke_current_tenant_account_sessions($1)", account_a),
            asyncpg.InsufficientPrivilegeError,
        )
        await runtime.execute("SELECT set_config('app.tenant_id','',false)")
        assert isinstance(
            await _assert_rejected(runtime, "SELECT revoke_current_tenant_account_sessions($1)", account_a),
            asyncpg.InsufficientPrivilegeError,
        )
        function_state = await owner.fetchrow(
            "SELECT prosecdef, proconfig, has_function_privilege('yimatong_app', p.oid, 'EXECUTE') AS runtime_execute, "
            "has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute "
            "FROM pg_proc p WHERE p.oid='public.revoke_current_tenant_account_sessions(uuid)'::regprocedure"
        )
        assert dict(function_state) == {
            "prosecdef": True,
            "proconfig": ["search_path=pg_catalog, public"],
            "runtime_execute": True,
            "public_execute": False,
        }
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN ('account_roles','role_permissions') "
                "AND column_name='tenant_id' AND data_type='uuid' AND is_nullable='NO'"
            )
            == 2
        )
        required_constraints = {
            "fk_accounts_tenant_organization",
            "account_roles_pkey",
            "ck_account_roles_tenant_id_nn",
            "fk_account_roles_tenant_account",
            "fk_account_roles_tenant_role",
            "role_permissions_pkey",
            "ck_role_permissions_tenant_id_nn",
            "fk_role_permissions_tenant_role",
            "fk_role_permissions_tenant_permission",
            "uq_roles_tenant_id_id",
            "uq_roles_tenant_name",
            "uq_permissions_tenant_id_id",
            "uq_permissions_tenant_code",
        }
        constraints = await owner.fetch(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE connamespace='public'::regnamespace AND conname=ANY($1::text[])",
            list(required_constraints),
        )
        assert {row["conname"] for row in constraints} == required_constraints
        assert all(row["convalidated"] for row in constraints)
        rls_state = await owner.fetch(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid IN ('public.account_roles'::regclass,'public.role_permissions'::regclass) ORDER BY relname"
        )
        assert [tuple(row) for row in rls_state] == [
            ("account_roles", True, True),
            ("role_permissions", True, True),
        ]
        policy_state = await owner.fetch(
            "SELECT tablename, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename IN ('account_roles','role_permissions') "
            "AND policyname='tenant_isolation' ORDER BY tablename"
        )
        assert len(policy_state) == 2
        assert all("tenant_id" in row["qual"] and "EXISTS" not in row["qual"].upper() for row in policy_state)
        assert all(
            "tenant_id" in row["with_check"] and "EXISTS" not in row["with_check"].upper() for row in policy_state
        )
        trigger_functions = await owner.fetch(
            "SELECT proname, prosecdef, proconfig, pg_get_userbyid(proowner) AS owner "
            "FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY($1::text[]) ORDER BY proname",
            [
                "assert_organization_hierarchy_acyclic",
                "assert_tenant_has_active_admin",
                "guard_account_role_governance",
                "guard_governance_identity_and_links",
                "guard_organization_hierarchy",
                "set_account_role_tenant_id",
                "set_role_permission_tenant_id",
            ],
        )
        assert len(trigger_functions) == 7
        assert all(row["prosecdef"] is False for row in trigger_functions)
        assert all(row["proconfig"] == ["search_path=pg_catalog, public"] for row in trigger_functions)
        assert all(row["owner"] != "yimatong_app" for row in trigger_functions)
    finally:
        await control.close()
        await runtime.close()
        await owner.close()


def test_sqlalchemy_metadata_matches_governance_schema(migrated_pg_url: str) -> None:
    _alembic_check(migrated_pg_url)


async def test_same_upgrade_commits_fence_before_online_validation(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    blocker = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await blocker.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", COORDINATOR_KEY)
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert "migration coordinator is already held" in failed.stdout + failed.stderr

        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FENCE_REVISION
            assert (
                await verifier.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name IN ('account_roles','role_permissions') "
                    "AND column_name='tenant_id' AND is_nullable='YES'"
                )
                == 2
            )
            tenant_id, _ = await _seed_tenant(verifier, "committed-fence")
            await _seed_role(verifier, tenant_id, "admin")
            rejected = await _assert_rejected(
                verifier,
                "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
                uuid.uuid4(),
                tenant_id,
            )
            assert isinstance(rejected, asyncpg.UniqueViolationError)
        finally:
            await verifier.close()
    finally:
        await blocker.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", COORDINATOR_KEY)
        await blocker.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_online_phase_restarts_locked_backfill_and_invalid_concurrent_index(
    migrated_pg_url: str,
) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        tenant_id, org_id = await _seed_tenant(owner, "online-restart")
        accounts = [
            await _seed_account(owner, tenant_id, org_id, "online-restart-a"),
            await _seed_account(owner, tenant_id, org_id, "online-restart-b"),
        ]
        admin_role = await _seed_role(owner, tenant_id, "admin")
        await _seed_role(owner, tenant_id, "operator")
        await owner.executemany(
            "INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)",
            [(account_id, admin_role) for account_id in accounts],
        )
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
    blocker = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    blocker_tx = blocker.transaction()
    await blocker_tx.start()
    try:
        await blocker.execute(
            "SELECT 1 FROM account_roles WHERE account_id=$1 AND role_id=$2 FOR UPDATE",
            accounts[0],
            admin_role,
        )
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "still has 1 NULL tenant_id rows after a SKIP LOCKED pass" in failed.stdout + failed.stderr
        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FENCE_REVISION
            assert await verifier.fetchval("SELECT count(*) FROM account_roles WHERE tenant_id IS NULL") == 1
            assert await verifier.fetchval("SELECT count(*) FROM account_roles WHERE tenant_id=$1", tenant_id) == 1
        finally:
            await verifier.close()
    finally:
        await blocker_tx.rollback()
        await blocker.close()

    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("CREATE UNIQUE INDEX CONCURRENTLY ux_roles_tenant_id_id_online ON roles (tenant_id,id)")
        await owner.execute(
            "ALTER TABLE roles ADD CONSTRAINT uq_roles_tenant_id_id UNIQUE USING INDEX ux_roles_tenant_id_id_online"
        )
        await owner.execute("CREATE INDEX CONCURRENTLY ix_account_roles_tenant_id ON account_roles (tenant_id)")
        await owner.execute(
            "ALTER TABLE accounts ADD CONSTRAINT fk_accounts_tenant_organization "
            "FOREIGN KEY (tenant_id,organization_id) REFERENCES organizations(tenant_id,id) NOT VALID"
        )
        await owner.execute(
            "ALTER TABLE account_roles ADD CONSTRAINT ck_account_roles_tenant_id_nn "
            "CHECK (tenant_id IS NOT NULL) NOT VALID"
        )
        assert await owner.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", COORDINATOR_KEY)
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "migration coordinator is already held" in failed.stdout + failed.stderr
        await owner.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", COORDINATOR_KEY)
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert (
            await verifier.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname IN ("
                "'uq_roles_tenant_id_id','fk_accounts_tenant_organization','ck_account_roles_tenant_id_nn')"
            )
            == 0
        )
        assert await verifier.fetchval("SELECT to_regclass('public.ix_account_roles_tenant_id')") is None
        assert (
            await verifier.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name IN ('account_roles','role_permissions') AND column_name='tenant_id'"
            )
            == 0
        )
    finally:
        await verifier.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    duplicate_operator = uuid.uuid4()
    try:
        await owner.execute("ALTER TABLE roles DISABLE TRIGGER guard_roles_governance")
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'operator',now(),now())",
            duplicate_operator,
            tenant_id,
        )
        await owner.execute("ALTER TABLE roles ENABLE TRIGGER guard_roles_governance")
        with pytest.raises(asyncpg.UniqueViolationError):
            await owner.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ux_roles_tenant_name_online ON roles (tenant_id DESC,name)"
            )
        await owner.execute("DELETE FROM roles WHERE id=$1", duplicate_operator)
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
    assert "Refusing to drop unexpected invalid index public.ux_roles_tenant_name_online" in (
        failed.stdout + failed.stderr
    )
    downgrade_failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert "Refusing to drop unexpected partial rollout index public.ux_roles_tenant_name_online" in (
        downgrade_failed.stdout + downgrade_failed.stderr
    )
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        desc_shell = await verifier.fetchrow(
            "SELECT index_row.indisvalid,index_row.indislive,ARRAY(SELECT option "
            "FROM unnest(index_row.indoption::smallint[]) WITH ORDINALITY AS opts(option,ordinality) "
            "WHERE opts.ordinality<=index_row.indnkeyatts ORDER BY opts.ordinality) AS key_options "
            "FROM pg_index AS index_row "
            "WHERE index_row.indexrelid='public.ux_roles_tenant_name_online'::regclass"
        )
        assert dict(desc_shell) == {"indisvalid": False, "indislive": True, "key_options": [3, 0]}
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FENCE_REVISION
        await verifier.execute("DROP INDEX CONCURRENTLY public.ux_roles_tenant_name_online")
    finally:
        await verifier.close()

    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("ALTER TABLE roles DISABLE TRIGGER guard_roles_governance")
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'operator',now(),now())",
            duplicate_operator,
            tenant_id,
        )
        await owner.execute("ALTER TABLE roles ENABLE TRIGGER guard_roles_governance")
        with pytest.raises(asyncpg.UniqueViolationError):
            await owner.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ux_roles_tenant_name_online ON roles (tenant_id,name)"
            )
        exact_invalid = await owner.fetchrow(
            "SELECT indisvalid,indisready,indislive FROM pg_index "
            "WHERE indexrelid='public.ux_roles_tenant_name_online'::regclass"
        )
        assert exact_invalid is not None and not exact_invalid["indisvalid"]
        await owner.executemany(
            "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
            "VALUES($1,$2,$3,'same-description',now(),now())",
            [
                (uuid.uuid4(), tenant_id, "wrong-shell:a"),
                (uuid.uuid4(), tenant_id, "wrong-shell:b"),
            ],
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await owner.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ux_permissions_tenant_code_online "
                "ON permissions (tenant_id,description)"
            )
        await owner.execute("DELETE FROM roles WHERE id=$1", duplicate_operator)
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
    assert "Refusing to drop unexpected invalid index public.ux_permissions_tenant_code_online" in (
        failed.stdout + failed.stderr
    )
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        wrong_shell = await verifier.fetchrow(
            "SELECT index_row.indisvalid,ARRAY(SELECT attr.attname "
            "FROM unnest(index_row.indkey::smallint[]) WITH ORDINALITY AS key(attnum,ordinality) "
            "JOIN pg_attribute AS attr ON attr.attrelid=index_row.indrelid AND attr.attnum=key.attnum "
            "WHERE key.ordinality<=index_row.indnkeyatts ORDER BY key.ordinality) AS columns "
            "FROM pg_index AS index_row "
            "WHERE index_row.indexrelid='public.ux_permissions_tenant_code_online'::regclass"
        )
        assert wrong_shell is not None
        assert wrong_shell["indisvalid"] is False
        assert wrong_shell["columns"] == ["tenant_id", "description"]
        await verifier.execute("DROP INDEX CONCURRENTLY public.ux_permissions_tenant_code_online")
    finally:
        await verifier.close()

    _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION)
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        exact = await verifier.fetchrow(
            "SELECT constraint_row.contype::text AS contype,index_row.indisvalid,index_row.indisready "
            "FROM pg_constraint AS constraint_row "
            "JOIN pg_index AS index_row ON index_row.indexrelid=constraint_row.conindid "
            "WHERE constraint_row.conname='uq_roles_tenant_name'"
        )
        assert dict(exact) == {"contype": "u", "indisvalid": True, "indisready": True}
    finally:
        await verifier.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FINAL_REVISION
        assert (
            await verifier.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname IN ("
                "'ck_account_roles_tenant_id_nn','ck_role_permissions_tenant_id_nn') AND convalidated"
            )
            == 2
        )
    finally:
        await verifier.close()


async def test_online_downgrade_preserves_unexpected_tenant_indexes_and_replays(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        async with owner.transaction():
            tenant_id, org_id = await _seed_tenant(owner, "tenant-index-downgrade")
            accounts = [
                await _seed_account(owner, tenant_id, org_id, "tenant-index-downgrade-a"),
                await _seed_account(owner, tenant_id, org_id, "tenant-index-downgrade-b"),
            ]
            admin_role = await _seed_role(owner, tenant_id, "admin")
            await owner.executemany(
                "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
                [(tenant_id, account_id, admin_role) for account_id in accounts],
            )
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("DROP INDEX CONCURRENTLY public.ix_account_roles_tenant_id")
        await owner.execute(
            "CREATE INDEX CONCURRENTLY ix_account_roles_tenant_id ON public.account_roles (tenant_id DESC)"
        )
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
    assert "Refusing to drop unexpected index public.ix_account_roles_tenant_id" in failed.stdout + failed.stderr
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        desc_index = await _index_fingerprint(verifier, "ix_account_roles_tenant_id")
        assert desc_index is not None
        assert dict(desc_index) == {
            "table_name": "account_roles",
            "indisvalid": True,
            "indisready": True,
            "indislive": True,
            "indisunique": False,
            "indisprimary": False,
            "indisexclusion": False,
            "indimmediate": True,
            "indnullsnotdistinct": False,
            "key_attribute_count": 1,
            "total_attribute_count": 1,
            "access_method": "btree",
            "has_no_predicate": True,
            "has_no_expressions": True,
            "has_no_includes": True,
            "uses_default_opclasses": True,
            "key_columns": ["tenant_id"],
            "key_options": [3],
            "key_collations": [0],
            "base_key_collations": [0],
        }
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
        await verifier.execute("DROP INDEX CONCURRENTLY public.ix_account_roles_tenant_id")
    finally:
        await verifier.close()

    _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("DROP INDEX CONCURRENTLY public.ix_account_roles_tenant_id")
        with pytest.raises(asyncpg.UniqueViolationError):
            await owner.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ix_account_roles_tenant_id ON public.account_roles (tenant_id)"
            )
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
    assert "Refusing to drop unexpected index public.ix_account_roles_tenant_id" in failed.stdout + failed.stderr
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        invalid_index = await _index_fingerprint(verifier, "ix_account_roles_tenant_id")
        assert invalid_index is not None
        assert invalid_index["table_name"] == "account_roles"
        assert invalid_index["indisvalid"] is False
        assert invalid_index["indisready"] is False
        assert invalid_index["indislive"] is True
        assert invalid_index["indisunique"] is True
        assert invalid_index["access_method"] == "btree"
        assert invalid_index["has_no_predicate"] is True
        assert invalid_index["has_no_expressions"] is True
        assert invalid_index["has_no_includes"] is True
        assert invalid_index["uses_default_opclasses"] is True
        assert invalid_index["key_columns"] == ["tenant_id"]
        assert invalid_index["key_options"] == [0]
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
        await verifier.execute("DROP INDEX CONCURRENTLY public.ix_account_roles_tenant_id")
    finally:
        await verifier.close()

    _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
    _alembic(migrated_pg_url, "upgrade", "head")
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        for index_name, table_name in (
            ("ix_account_roles_tenant_id", "account_roles"),
            ("ix_role_permissions_tenant_id", "role_permissions"),
        ):
            exact_index = await _index_fingerprint(verifier, index_name)
            assert exact_index is not None
            assert exact_index["table_name"] == table_name
            assert exact_index["indisvalid"] and exact_index["indisready"] and exact_index["indislive"]
            assert exact_index["indisunique"] is False
            assert exact_index["access_method"] == "btree"
            assert exact_index["has_no_predicate"] is True
            assert exact_index["has_no_expressions"] is True
            assert exact_index["has_no_includes"] is True
            assert exact_index["uses_default_opclasses"] is True
            assert exact_index["key_columns"] == ["tenant_id"]
            assert exact_index["key_options"] == [0]
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FINAL_REVISION
    finally:
        await verifier.close()

    _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await verifier.fetchval("SELECT to_regclass('public.ix_account_roles_tenant_id')") is None
        assert await verifier.fetchval("SELECT to_regclass('public.ix_role_permissions_tenant_id')") is None
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FENCE_REVISION
    finally:
        await verifier.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_constraint_fingerprints_fail_closed_and_preserve_unexpected_objects(migrated_pg_url: str) -> None:
    wrong_constraints: set[tuple[str, str]] = set()
    primary_error = None

    async def drop_wrong(table: str, constraint: str) -> None:
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        finally:
            await owner.close()
        wrong_constraints.discard((table, constraint))

    try:
        _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "ALTER TABLE public.account_roles ADD CONSTRAINT ck_account_roles_tenant_id_nn "
                "CHECK (tenant_id IS NOT NULL OR tenant_id IS NULL) NOT VALID"
            )
            wrong_constraints.add(("account_roles", "ck_account_roles_tenant_id_nn"))
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to reuse unexpected constraint public.account_roles.ck_account_roles_tenant_id_nn" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            tautology = await _constraint_fingerprint(owner, "account_roles", "ck_account_roles_tenant_id_nn")
            assert tautology is not None and tautology["contype"] == "c"
            assert tautology["convalidated"] is False
            assert " OR " in tautology["check_expression"]
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout constraint" in failed.stdout + failed.stderr
        await drop_wrong("account_roles", "ck_account_roles_tenant_id_nn")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)

        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "ALTER TABLE public.accounts ADD CONSTRAINT fk_accounts_tenant_organization "
                "FOREIGN KEY (tenant_id,organization_id) REFERENCES public.organizations(tenant_id,id) "
                "MATCH FULL ON UPDATE RESTRICT ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED NOT VALID"
            )
            wrong_constraints.add(("accounts", "fk_accounts_tenant_organization"))
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to reuse unexpected constraint public.accounts.fk_accounts_tenant_organization" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            wrong_fk = await _constraint_fingerprint(owner, "accounts", "fk_accounts_tenant_organization")
            assert wrong_fk is not None
            assert dict(wrong_fk)["convalidated"] is False
            assert (wrong_fk["confmatchtype"], wrong_fk["confupdtype"], wrong_fk["confdeltype"]) == (
                "f",
                "r",
                "c",
            )
            assert wrong_fk["condeferrable"] and wrong_fk["condeferred"]
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout constraint" in failed.stdout + failed.stderr
        await drop_wrong("accounts", "fk_accounts_tenant_organization")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)

        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "ALTER TABLE public.roles ADD CONSTRAINT uq_roles_tenant_name "
                "UNIQUE (tenant_id,name) DEFERRABLE INITIALLY DEFERRED"
            )
            wrong_constraints.add(("roles", "uq_roles_tenant_name"))
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to reuse unexpected constraint public.roles.uq_roles_tenant_name" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            wrong_unique = await _constraint_fingerprint(owner, "roles", "uq_roles_tenant_name")
            assert wrong_unique is not None
            assert wrong_unique["columns"] == ["tenant_id", "name"]
            assert wrong_unique["condeferrable"] and wrong_unique["condeferred"]
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout constraint" in failed.stdout + failed.stderr
        await drop_wrong("roles", "uq_roles_tenant_name")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        _alembic(migrated_pg_url, "upgrade", "head")

        _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute("ALTER TABLE public.account_roles DROP CONSTRAINT ck_account_roles_tenant_id_nn")
            await owner.execute(
                "ALTER TABLE public.account_roles ADD CONSTRAINT ck_account_roles_tenant_id_nn "
                "CHECK (tenant_id IS NOT NULL OR tenant_id IS NULL)"
            )
            wrong_constraints.add(("account_roles", "ck_account_roles_tenant_id_nn"))
            await owner.execute("ALTER TABLE public.accounts DROP CONSTRAINT fk_accounts_tenant_organization")
            await owner.execute(
                "ALTER TABLE public.accounts ADD CONSTRAINT fk_accounts_tenant_organization "
                "FOREIGN KEY (tenant_id,organization_id) REFERENCES public.organizations(tenant_id,id) "
                "MATCH FULL ON UPDATE RESTRICT ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED"
            )
            wrong_constraints.add(("accounts", "fk_accounts_tenant_organization"))
            await owner.execute("ALTER TABLE public.roles DROP CONSTRAINT uq_roles_tenant_name")
            await owner.execute(
                "ALTER TABLE public.roles ADD CONSTRAINT uq_roles_tenant_name UNIQUE (tenant_id,name,id)"
            )
            wrong_constraints.add(("roles", "uq_roles_tenant_name"))
        finally:
            await owner.close()

        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert "without exact validated CHECK public.account_roles.ck_account_roles_tenant_id_nn" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM pg_attribute WHERE attrelid IN ("
                    "'public.account_roles'::regclass,'public.role_permissions'::regclass) "
                    "AND attname='tenant_id' AND attnotnull"
                )
                == 0
            )
        finally:
            await owner.close()

        failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
        assert "Refusing to drop unexpected constraint public.account_roles.ck_account_roles_tenant_id_nn" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong("account_roles", "ck_account_roles_tenant_id_nn")
        failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
        assert "Refusing to drop unexpected constraint public.accounts.fk_accounts_tenant_organization" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong("accounts", "fk_accounts_tenant_organization")
        failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
        assert "Refusing to drop unexpected constraint public.roles.uq_roles_tenant_name" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong("roles", "uq_roles_tenant_name")
        _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
        _alembic(migrated_pg_url, "upgrade", "head")

        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            for table, constraint in (
                ("account_roles", "ck_account_roles_tenant_id_nn"),
                ("role_permissions", "ck_role_permissions_tenant_id_nn"),
            ):
                exact_check = await _constraint_fingerprint(owner, table, constraint)
                assert exact_check is not None and exact_check["convalidated"]
                assert exact_check["columns"] == ["tenant_id"]
                assert " OR " not in exact_check["check_expression"]
            exact_fk = await _constraint_fingerprint(owner, "accounts", "fk_accounts_tenant_organization")
            assert exact_fk is not None and exact_fk["convalidated"]
            assert (exact_fk["confmatchtype"], exact_fk["confupdtype"], exact_fk["confdeltype"]) == (
                "s",
                "a",
                "a",
            )
            assert not exact_fk["condeferrable"] and not exact_fk["condeferred"]
            exact_unique = await _constraint_fingerprint(owner, "roles", "uq_roles_tenant_name")
            assert exact_unique is not None
            assert exact_unique["columns"] == ["tenant_id", "name"]
            assert not exact_unique["condeferrable"] and not exact_unique["condeferred"]
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM pg_attribute WHERE attrelid IN ("
                    "'public.account_roles'::regclass,'public.role_permissions'::regclass) "
                    "AND attname='tenant_id' AND attnotnull"
                )
                == 2
            )
        finally:
            await owner.close()
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if wrong_constraints:
            owner = None
            try:
                owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
                for table, constraint in wrong_constraints:
                    await owner.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                if owner is not None:
                    await owner.close()
        try:
            _alembic(migrated_pg_url, "upgrade", "head")
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            await _replay_runtime_role_acl(migrated_pg_url)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is None:
                raise cleanup_errors[0]
            for cleanup_error in cleanup_errors:
                primary_error.add_note(f"constraint fingerprint cleanup also failed: {cleanup_error!r}")


async def test_index_collation_and_null_distinctness_fingerprints_fail_closed(migrated_pg_url: str) -> None:
    wrong_indexes: set[str] = set()
    wrong_constraints: set[tuple[str, str]] = set()
    primary_error = None
    duplicate_role: uuid.UUID | None = None

    async def drop_wrong_index(index_name: str) -> None:
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
        finally:
            await owner.close()
        wrong_indexes.discard(index_name)

    async def drop_wrong_constraint(table: str, constraint: str) -> None:
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        finally:
            await owner.close()
        wrong_constraints.discard((table, constraint))

    try:
        _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ux_roles_tenant_name_online "
                'ON public.roles (tenant_id,name COLLATE "C")'
            )
            wrong_indexes.add("ux_roles_tenant_name_online")
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to reuse unexpected valid index public.ux_roles_tenant_name_online" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            collated = await _index_fingerprint(owner, "ux_roles_tenant_name_online")
            assert collated is not None and collated["indisvalid"] and collated["indislive"]
            assert collated["key_columns"] == ["tenant_id", "name"]
            assert collated["key_collations"][0] == collated["base_key_collations"][0] == 0
            assert collated["key_collations"][1] != collated["base_key_collations"][1]
            assert collated["indnullsnotdistinct"] is False
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout index public.ux_roles_tenant_name_online" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong_index("ux_roles_tenant_name_online")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)

        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        duplicate_role = uuid.uuid4()
        try:
            tenant_id, _ = await _seed_tenant(owner, "index-nnd-invalid")
            await _seed_role(owner, tenant_id, "operator")
            await owner.execute("ALTER TABLE public.roles DISABLE TRIGGER guard_roles_governance")
            await owner.execute(
                "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'operator',now(),now())",
                duplicate_role,
                tenant_id,
            )
            await owner.execute("ALTER TABLE public.roles ENABLE TRIGGER guard_roles_governance")
            with pytest.raises(asyncpg.UniqueViolationError):
                await owner.execute(
                    "CREATE UNIQUE INDEX CONCURRENTLY ux_roles_tenant_name_online "
                    "ON public.roles (tenant_id,name) NULLS NOT DISTINCT"
                )
            wrong_indexes.add("ux_roles_tenant_name_online")
            await owner.execute("DELETE FROM public.roles WHERE id=$1", duplicate_role)
            duplicate_role = None
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to drop unexpected invalid index public.ux_roles_tenant_name_online" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            invalid_nnd = await _index_fingerprint(owner, "ux_roles_tenant_name_online")
            assert invalid_nnd is not None
            assert invalid_nnd["indisvalid"] is False and invalid_nnd["indislive"] is True
            assert invalid_nnd["indnullsnotdistinct"] is True
            assert invalid_nnd["key_collations"] == invalid_nnd["base_key_collations"]
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout index public.ux_roles_tenant_name_online" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong_index("ux_roles_tenant_name_online")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)

        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "ALTER TABLE public.roles ADD CONSTRAINT uq_roles_tenant_name "
                "UNIQUE NULLS NOT DISTINCT (tenant_id,name)"
            )
            wrong_constraints.add(("roles", "uq_roles_tenant_name"))
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "upgrade", ONLINE_REVISION, succeeds=False)
        assert "Refusing to reuse unexpected constraint public.roles.uq_roles_tenant_name" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            backing_nnd = await _index_fingerprint(owner, "uq_roles_tenant_name")
            assert backing_nnd is not None and backing_nnd["indnullsnotdistinct"] is True
            assert backing_nnd["key_collations"] == backing_nnd["base_key_collations"]
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Refusing to drop unexpected partial rollout constraint public.roles.uq_roles_tenant_name" in (
            failed.stdout + failed.stderr
        )
        await drop_wrong_constraint("roles", "uq_roles_tenant_name")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        _alembic(migrated_pg_url, "upgrade", "head")

        _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute("ALTER TABLE public.permissions DROP CONSTRAINT uq_permissions_tenant_code")
            await owner.execute(
                "ALTER TABLE public.permissions ADD CONSTRAINT uq_permissions_tenant_code "
                "UNIQUE NULLS NOT DISTINCT (tenant_id,code)"
            )
            wrong_constraints.add(("permissions", "uq_permissions_tenant_code"))
        finally:
            await owner.close()
        failed = _alembic(migrated_pg_url, "downgrade", FENCE_REVISION, succeeds=False)
        assert "Refusing to drop unexpected constraint public.permissions.uq_permissions_tenant_code" in (
            failed.stdout + failed.stderr
        )
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            normal_nnd = await _index_fingerprint(owner, "uq_permissions_tenant_code")
            assert normal_nnd is not None and normal_nnd["indnullsnotdistinct"] is True
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
        finally:
            await owner.close()
        await drop_wrong_constraint("permissions", "uq_permissions_tenant_code")
        _alembic(migrated_pg_url, "downgrade", FENCE_REVISION)
        _alembic(migrated_pg_url, "upgrade", "head")

        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            for index_name in (
                "uq_roles_tenant_name",
                "uq_permissions_tenant_code",
                "ix_account_roles_tenant_id",
                "ix_role_permissions_tenant_id",
            ):
                exact = await _index_fingerprint(owner, index_name)
                assert exact is not None
                assert exact["indisvalid"] and exact["indisready"] and exact["indislive"]
                assert not exact["indisprimary"] and not exact["indisexclusion"]
                assert exact["indimmediate"] and not exact["indnullsnotdistinct"]
                assert exact["key_attribute_count"] == exact["total_attribute_count"]
                assert exact["key_collations"] == exact["base_key_collations"]
                assert exact["has_no_predicate"] and exact["has_no_expressions"]
                assert exact["has_no_includes"] and exact["uses_default_opclasses"]
        finally:
            await owner.close()
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        owner = None
        try:
            owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
            for table, constraint in wrong_constraints:
                await owner.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {constraint}")
            if duplicate_role is not None:
                await owner.execute("DELETE FROM public.roles WHERE id=$1", duplicate_role)
        except BaseException as exc:
            cleanup_errors.append(exc)
        finally:
            if owner is not None:
                await owner.close()
        for index_name in tuple(wrong_indexes):
            try:
                await drop_wrong_index(index_name)
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            _alembic(migrated_pg_url, "upgrade", "head")
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            await _replay_runtime_role_acl(migrated_pg_url)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is None:
                raise cleanup_errors[0]
            for cleanup_error in cleanup_errors:
                primary_error.add_note(f"index semantic fingerprint cleanup also failed: {cleanup_error!r}")


async def test_backfill_uses_global_tenant_mapping_lock_order_in_both_orders(migrated_pg_url: str) -> None:
    first_app = None
    first_observer = None
    first_process = None
    second_app = None
    second_observer = None
    second_process = None
    app_mutation = None
    primary_error = None
    try:
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            tenant_id, org_id = await _seed_tenant(owner, "backfill-lock-order")
            account_id = await _seed_account(owner, tenant_id, org_id, "backfill-lock-order")
            admin_role = await _seed_role(owner, tenant_id, "admin")
            unrelated_accounts = [
                await _seed_account(owner, tenant_id, org_id, f"backfill-unrelated-{index}") for index in range(12)
            ]
            await owner.executemany(
                "INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)",
                [(account_id, admin_role), *((item, admin_role) for item in unrelated_accounts)],
            )
        finally:
            await owner.close()
        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)

        first_app = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        first_observer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        first_tx = first_app.transaction()
        await first_tx.start()
        await first_app.execute("SELECT pg_advisory_xact_lock_shared($1)", QUOTA_ROLLOUT_ADVISORY_LOCK_KEY)
        await first_app.execute("SELECT 1 FROM tenants WHERE id=$1 FOR UPDATE", tenant_id)
        first_process = await _start_alembic(migrated_pg_url, "upgrade", ONLINE_REVISION)
        for _ in range(100):
            migration_waits_for_tenant = await first_observer.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                "AND query LIKE '%candidate_tenants AS MATERIALIZED%' AND wait_event_type='Lock')"
            )
            if migration_waits_for_tenant:
                break
            await asyncio.sleep(0.05)
        assert migration_waits_for_tenant
        started = time.monotonic()
        await asyncio.wait_for(
            first_app.execute("DELETE FROM account_roles WHERE account_id=$1 AND role_id=$2", account_id, admin_role),
            timeout=2,
        )
        assert time.monotonic() - started < 2
        await first_tx.rollback()
        stdout, stderr = await asyncio.wait_for(first_process.communicate(), timeout=10)
        assert first_process.returncode == 0, stdout.decode() + stderr.decode()
        first_process = None
        await first_observer.close()
        first_observer = None
        await first_app.close()
        first_app = None

        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await verifier.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
            assert (
                await verifier.fetchval(
                    "SELECT tenant_id FROM account_roles WHERE account_id=$1 AND role_id=$2",
                    account_id,
                    admin_role,
                )
                == tenant_id
            )
        finally:
            await verifier.close()

        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        _alembic(migrated_pg_url, "upgrade", FENCE_REVISION)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            await owner.execute(
                "CREATE FUNCTION public.zz_test_pause_backfill() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER "
                "SET search_path=pg_catalog,public AS $$BEGIN PERFORM pg_sleep(1); RETURN NEW; END$$"
            )
            await owner.execute(
                "CREATE TRIGGER zz_test_pause_backfill AFTER UPDATE OF tenant_id ON account_roles "
                f"FOR EACH ROW WHEN (OLD.tenant_id IS NULL AND OLD.account_id='{account_id}'::uuid "
                f"AND OLD.role_id='{admin_role}'::uuid) EXECUTE FUNCTION public.zz_test_pause_backfill()"
            )
        finally:
            await owner.close()

        second_app = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        second_observer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        second_tx = second_app.transaction()
        await second_tx.start()
        second_process = await _start_alembic(migrated_pg_url, "upgrade", ONLINE_REVISION)

        async def mutate_after_migration_lock() -> None:
            await second_app.execute("SELECT pg_advisory_xact_lock_shared($1)", QUOTA_ROLLOUT_ADVISORY_LOCK_KEY)
            await second_app.execute("SELECT 1 FROM tenants WHERE id=$1 FOR UPDATE", tenant_id)
            await second_app.execute(
                "UPDATE account_roles SET tenant_id=tenant_id WHERE account_id=$1 AND role_id=$2",
                account_id,
                admin_role,
            )

        for _ in range(100):
            migration_holds_tenant_and_mapping = await second_observer.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event='PgSleep' "
                "AND query LIKE '%candidate_tenants AS MATERIALIZED%')"
            )
            if migration_holds_tenant_and_mapping:
                break
            await asyncio.sleep(0.05)
        assert migration_holds_tenant_and_mapping
        app_mutation = asyncio.create_task(mutate_after_migration_lock())
        await asyncio.sleep(0.1)
        assert not app_mutation.done()
        await asyncio.wait_for(app_mutation, timeout=8)
        app_mutation = None
        await second_tx.rollback()
        stdout, stderr = await asyncio.wait_for(second_process.communicate(), timeout=10)
        assert second_process.returncode == 0, stdout.decode() + stderr.decode()
        second_process = None
        await _drop_backfill_pause_objects(migrated_pg_url)

        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == ONLINE_REVISION
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM account_roles WHERE tenant_id=$1 AND account_id=$2 AND role_id=$3",
                    tenant_id,
                    account_id,
                    admin_role,
                )
                == 1
            )
            assert await owner.fetchval("SELECT count(*) FROM account_roles WHERE tenant_id=$1", tenant_id) == 13
            assert await owner.fetchval("SELECT count(*) FROM account_roles WHERE tenant_id IS NULL") == 0
        finally:
            await owner.close()

        _alembic(migrated_pg_url, "upgrade", "head")
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        _alembic(migrated_pg_url, "upgrade", "head")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        try:
            if app_mutation is not None:
                if not app_mutation.done():
                    app_mutation.cancel()
                try:
                    await app_mutation
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    cleanup_errors.append(exc)
        except BaseException as exc:
            cleanup_errors.append(exc)
        for process in (second_process, first_process):
            try:
                await _stop_alembic_process(process)
            except BaseException as exc:
                cleanup_errors.append(exc)
        for connection in (second_observer, second_app, first_observer, first_app):
            try:
                if connection is not None and not connection.is_closed():
                    await connection.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            await _drop_backfill_pause_objects(migrated_pg_url)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            _alembic(migrated_pg_url, "upgrade", "head")
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            await _replay_runtime_role_acl(migrated_pg_url)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is None:
                raise cleanup_errors[0]
            for cleanup_error in cleanup_errors:
                primary_error.add_note(f"backfill lock-order cleanup also failed: {cleanup_error!r}")


async def test_final_phase_lock_timeout_retry_and_migration_first_writer_queue(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        async with owner.transaction():
            tenant_id, org_id = await _seed_tenant(owner, "final-lock")
            account_id = await _seed_account(owner, tenant_id, org_id, "final-lock-admin")
            admin_role = await _seed_role(owner, tenant_id, "admin")
            await owner.execute("INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)", account_id, admin_role)
            peer_org = uuid.uuid4()
            await owner.execute(
                "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) "
                "VALUES($1,$2,'final-lock-peer',now(),now())",
                peer_org,
                tenant_id,
            )
    finally:
        await owner.close()

    writer_cases = (
        ("DELETE FROM account_roles WHERE account_id=$1 AND role_id=$2", (account_id, admin_role)),
        ("UPDATE accounts SET is_active=false WHERE id=$1", (account_id,)),
        (
            "UPDATE organizations SET parent_id=CASE WHEN id=$1 THEN $2 ELSE $1 END WHERE id IN ($1,$2)",
            (org_id, peer_org),
        ),
    )
    for statement, args in writer_cases:
        writer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        writer_tx = writer.transaction()
        await writer_tx.start()
        try:
            await writer.execute(statement, *args)
            started = time.monotonic()
            failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
            elapsed = time.monotonic() - started
            assert 4.0 <= elapsed < 12.0
            assert "lock timeout" in (failed.stdout + failed.stderr).lower()
            with pytest.raises(asyncpg.CheckViolationError):
                await writer_tx.commit()
        finally:
            if not writer.is_closed():
                await writer.close()
        _alembic(migrated_pg_url, "upgrade", "head")
        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FINAL_REVISION
            assert (
                await verifier.fetchval(
                    "SELECT count(*) FROM accounts AS account "
                    "JOIN account_roles AS mapping ON mapping.account_id=account.id "
                    "JOIN roles AS role ON role.id=mapping.role_id "
                    "WHERE account.tenant_id=$1 AND account.is_active AND role.name='admin'",
                    tenant_id,
                )
                == 1
            )
            assert not await verifier.fetchval(
                "WITH RECURSIVE walk(id,parent_id,path,cycle) AS ("
                "SELECT id,parent_id,ARRAY[id],false FROM organizations WHERE tenant_id=$1 "
                "UNION ALL SELECT organization.id,organization.parent_id,walk.path||organization.id,"
                "organization.id=ANY(walk.path) FROM organizations AS organization "
                "JOIN walk ON organization.id=walk.parent_id "
                "WHERE organization.tenant_id=$1 AND NOT walk.cycle) SELECT bool_or(cycle) FROM walk",
                tenant_id,
            )
        finally:
            await verifier.close()
        if statement != writer_cases[-1][0]:
            _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)

    _alembic(migrated_pg_url, "downgrade", ONLINE_REVISION)
    reader = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    observer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    writer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    reader_tx = reader.transaction()
    writer_tx = writer.transaction()
    await reader_tx.start()
    await reader.execute("SELECT count(*) FROM account_roles")
    process = await _start_alembic(migrated_pg_url, "upgrade", "head")
    try:
        for _ in range(80):
            finalizer_waiting = await observer.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_locks AS held "
                "JOIN pg_locks AS waiting ON waiting.pid=held.pid "
                "WHERE held.relation='public.account_roles'::regclass "
                "AND held.mode='ShareRowExclusiveLock' AND held.granted "
                "AND waiting.relation='public.account_roles'::regclass "
                "AND waiting.mode='AccessExclusiveLock' AND NOT waiting.granted)"
            )
            if finalizer_waiting:
                break
            await asyncio.sleep(0.05)
        assert finalizer_waiting
        await writer_tx.start()
        blocked_delete = asyncio.create_task(
            writer.execute("DELETE FROM account_roles WHERE account_id=$1 AND role_id=$2", account_id, admin_role)
        )
        await asyncio.sleep(0.1)
        assert not blocked_delete.done()
        await reader_tx.commit()
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        assert process.returncode == 0, stdout.decode() + stderr.decode()
        await blocked_delete
        with pytest.raises(asyncpg.CheckViolationError):
            await writer_tx.commit()
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == FINAL_REVISION
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM accounts AS account "
                "JOIN account_roles AS mapping ON mapping.account_id=account.id "
                "JOIN roles AS role ON role.id=mapping.role_id "
                "WHERE account.tenant_id=$1 AND account.is_active AND role.name='admin'",
                tenant_id,
            )
            == 1
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await writer.close()
        await observer.close()
        await reader.close()


@pytest.mark.parametrize("first_index", [0, 1])
async def test_concurrent_admin_removal_never_commits_zero_admins(migrated_pg_url: str, first_index: int) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    async with owner.transaction():
        tenant_id, org_id = await _seed_tenant(owner, f"admin-race-{first_index}")
        accounts = [
            await _seed_account(owner, tenant_id, org_id, f"admin-{first_index}-a"),
            await _seed_account(owner, tenant_id, org_id, f"admin-{first_index}-b"),
        ]
        role_id = await _seed_role(owner, tenant_id, "admin")
        await owner.executemany(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            [(tenant_id, account_id, role_id) for account_id in accounts],
        )
    await owner.close()
    first = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    second = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        for conn in (first, second):
            await conn.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
            await conn.execute("SELECT set_config('app.bypass_rls','false',false)")
        first_tx = first.transaction()
        second_tx = second.transaction()
        await first_tx.start()
        await second_tx.start()
        await first.execute(
            "DELETE FROM account_roles WHERE account_id=$1 AND role_id=$2", accounts[first_index], role_id
        )
        blocked_delete = asyncio.create_task(
            second.execute(
                "DELETE FROM account_roles WHERE account_id=$1 AND role_id=$2", accounts[1 - first_index], role_id
            )
        )
        await asyncio.sleep(0.1)
        assert not blocked_delete.done()
        await first_tx.commit()
        await blocked_delete
        with pytest.raises(asyncpg.CheckViolationError):
            await second_tx.commit()
        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert (
                await verifier.fetchval(
                    "SELECT count(*) FROM accounts a JOIN account_roles ar "
                    "ON ar.tenant_id=a.tenant_id AND ar.account_id=a.id "
                    "JOIN roles r ON r.tenant_id=ar.tenant_id AND r.id=ar.role_id "
                    "WHERE a.tenant_id=$1 AND a.is_active AND r.name='admin'",
                    tenant_id,
                )
                == 1
            )
        finally:
            await verifier.close()
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("first_index", [0, 1])
async def test_concurrent_admin_disable_never_commits_zero_active_admins(
    migrated_pg_url: str, first_index: int
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    async with owner.transaction():
        tenant_id, org_id = await _seed_tenant(owner, f"admin-disable-race-{first_index}")
        accounts = [
            await _seed_account(owner, tenant_id, org_id, f"disable-{first_index}-a"),
            await _seed_account(owner, tenant_id, org_id, f"disable-{first_index}-b"),
        ]
        role_id = await _seed_role(owner, tenant_id, "admin")
        await owner.executemany(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            [(tenant_id, account_id, role_id) for account_id in accounts],
        )
    await owner.close()
    first = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    second = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        for conn in (first, second):
            await conn.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
            await conn.execute("SELECT set_config('app.bypass_rls','false',false)")
        first_tx = first.transaction()
        second_tx = second.transaction()
        await first_tx.start()
        await second_tx.start()
        await first.execute("UPDATE accounts SET is_active=false WHERE id=$1", accounts[first_index])
        blocked_disable = asyncio.create_task(
            second.execute("UPDATE accounts SET is_active=false WHERE id=$1", accounts[1 - first_index])
        )
        await asyncio.sleep(0.1)
        assert not blocked_disable.done()
        await first_tx.commit()
        await blocked_disable
        with pytest.raises(asyncpg.CheckViolationError):
            await second_tx.commit()
        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert (
                await verifier.fetchval(
                    "SELECT count(*) FROM accounts a JOIN account_roles ar "
                    "ON ar.tenant_id=a.tenant_id AND ar.account_id=a.id "
                    "JOIN roles r ON r.tenant_id=ar.tenant_id AND r.id=ar.role_id "
                    "WHERE a.tenant_id=$1 AND a.is_active AND r.name='admin'",
                    tenant_id,
                )
                == 1
            )
        finally:
            await verifier.close()
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("first_index", [0, 1])
async def test_concurrent_opposite_parent_updates_cannot_commit_cycle(migrated_pg_url: str, first_index: int) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    tenant_id, _ = await _seed_tenant(owner, f"org-race-{first_index}")
    organizations = [uuid.uuid4(), uuid.uuid4()]
    await owner.executemany(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        [(organizations[0], tenant_id, "left"), (organizations[1], tenant_id, "right")],
    )
    await owner.close()
    first = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    second = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        for conn in (first, second):
            await conn.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
            await conn.execute("SELECT set_config('app.bypass_rls','false',false)")
        first_tx = first.transaction()
        second_tx = second.transaction()
        await first_tx.start()
        await second_tx.start()
        first_child = organizations[first_index]
        first_parent = organizations[1 - first_index]
        await first.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", first_parent, first_child)
        blocked_update = asyncio.create_task(
            second.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", first_child, first_parent)
        )
        await asyncio.sleep(0.1)
        assert not blocked_update.done()
        await first_tx.commit()
        await blocked_update
        with pytest.raises(asyncpg.CheckViolationError):
            await second_tx.commit()
        verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            cycle = await verifier.fetchval(
                "WITH RECURSIVE walk(id,parent_id,path,cycle) AS ("
                "SELECT id,parent_id,ARRAY[id],false FROM organizations WHERE tenant_id=$1 "
                "UNION ALL SELECT o.id,o.parent_id,w.path||o.id,o.id=ANY(w.path) "
                "FROM organizations o JOIN walk w ON o.id=w.parent_id "
                "WHERE o.tenant_id=$1 AND NOT w.cycle) SELECT bool_or(cycle) FROM walk",
                tenant_id,
            )
            assert cycle is False
        finally:
            await verifier.close()
    finally:
        await first.close()
        await second.close()


async def test_preflight_reports_exact_drift_and_downgrade_reupgrade(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN ('account_roles','role_permissions') "
                "AND column_name='tenant_id'"
            )
            == 0
        )
        legacy_constraint_names = {
            "accounts_organization_id_fkey",
            "account_roles_account_id_fkey",
            "account_roles_role_id_fkey",
            "role_permissions_role_id_fkey",
            "role_permissions_permission_id_fkey",
        }
        legacy_constraints = await owner.fetch(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE connamespace='public'::regnamespace AND conname=ANY($1::text[])",
            list(legacy_constraint_names),
        )
        assert {row["conname"] for row in legacy_constraints} == legacy_constraint_names
        assert all(row["convalidated"] for row in legacy_constraints)
        primary_keys = await owner.fetch(
            "SELECT conrelid::regclass::text AS table_name, pg_get_constraintdef(oid) AS definition "
            "FROM pg_constraint WHERE conname IN ('account_roles_pkey','role_permissions_pkey') ORDER BY conname"
        )
        assert {row["table_name"]: row["definition"] for row in primary_keys} == {
            "account_roles": "PRIMARY KEY (account_id, role_id)",
            "role_permissions": "PRIMARY KEY (role_id, permission_id)",
        }
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace "
                "AND conname IN ('fk_accounts_tenant_organization','uq_roles_tenant_name',"
                "'uq_permissions_tenant_code','uq_roles_tenant_id_id','uq_permissions_tenant_id_id')"
            )
            == 0
        )
        assert (
            await owner.fetchval("SELECT to_regprocedure('public.revoke_current_tenant_account_sessions(uuid)')")
            is None
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN ("
                "'set_account_role_tenant_id','set_role_permission_tenant_id','lock_account_role_governance',"
                "'assert_admin_after_account_role_change','lock_account_governance','assert_admin_after_account_change',"
                "'lock_role_governance','assert_admin_after_role_change','lock_organization_hierarchy',"
                "'assert_organization_hierarchy_acyclic')"
            )
            == 0
        )
        downgraded_policies = await owner.fetch(
            "SELECT tablename, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename IN ('account_roles','role_permissions') "
            "AND policyname='tenant_isolation' ORDER BY tablename"
        )
        assert len(downgraded_policies) == 2
        assert all("EXISTS" in row["qual"].upper() for row in downgraded_policies)
        assert all("EXISTS" in row["with_check"].upper() for row in downgraded_policies)

        tenant_a, org_a = await _seed_tenant(owner, "preflight-a")
        tenant_b, org_b = await _seed_tenant(owner, "preflight-b")
        account_without_admin = await _seed_account(owner, tenant_a, org_a, "preflight-no-admin")
        duplicate_ids = [uuid.uuid4(), uuid.uuid4()]
        await owner.executemany(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
            [(duplicate_ids[0], tenant_a), (duplicate_ids[1], tenant_a)],
        )
        cross_account = await _seed_account(owner, tenant_a, org_a, "preflight-cross")
        await owner.execute("UPDATE accounts SET organization_id=$1 WHERE id=$2", org_b, cross_account)
        cycle_peer = uuid.uuid4()
        await owner.execute(
            "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'cycle-peer',now(),now())",
            cycle_peer,
            tenant_a,
        )
        await owner.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", cycle_peer, org_a)
        await owner.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", org_a, cycle_peer)

        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        output = failed.stdout + failed.stderr
        assert "tenants with accounts but no active administrator" in output
        assert str(tenant_a) in output and str(account_without_admin) in output
        assert "'account_count': 2" in output and "'active_admin_count': 0" in output
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == FENCE_REVISION
        await owner.execute(
            "INSERT INTO account_roles(account_id,role_id) VALUES($1,$2)",
            account_without_admin,
            duplicate_ids[0],
        )
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        output = failed.stdout + failed.stderr
        assert "duplicate tenant role names" in output
        assert all(str(item) in output for item in duplicate_ids)
        await owner.execute("DELETE FROM roles WHERE id=$1", duplicate_ids[1])
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        output = failed.stdout + failed.stderr
        assert "cross-tenant account organization links" in output
        assert str(cross_account) in output
        assert str(tenant_a) in output and str(tenant_b) in output
        await owner.execute("UPDATE accounts SET organization_id=$1 WHERE id=$2", org_a, cross_account)
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        output = failed.stdout + failed.stderr
        assert "cyclic organization hierarchies" in output
        assert str(org_a) in output and str(cycle_peer) in output
        await owner.execute("UPDATE organizations SET parent_id=NULL WHERE id IN ($1,$2)", org_a, cycle_peer)
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    verifier = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await verifier.fetchval("SELECT version_num FROM alembic_version") == FINAL_REVISION
        assert (
            await verifier.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN ('account_roles','role_permissions') "
                "AND column_name='tenant_id' AND is_nullable='NO'"
            )
            == 2
        )
    finally:
        await verifier.close()
