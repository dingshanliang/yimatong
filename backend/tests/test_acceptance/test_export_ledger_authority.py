"""PostgreSQL authority gates for immutable prepared-export evidence."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.services.auth import authenticate_login
from app.utils.security import hash_password
from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
)
from tests.test_acceptance.test_code_batch_delivery_contract import (
    _insert_batch,
    _insert_items,
    _insert_receipt,
    _purge_owned_delivery_fixture,
    _seed_catalog,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

CALL = """SELECT * FROM public.record_prepared_export(
  $1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)"""


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _control_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "acceptance_control:control_pwd@")


class _AllowLoginCache:
    async def rate_limit_check_shared(self, _key: str, *, max_attempts: int, window_seconds: int):
        del window_seconds
        return True, max_attempts


async def _grant_permission(owner, ids, code: str, *, role_id: uuid.UUID | None = None) -> None:
    permission_id = await owner.fetchval(
        "SELECT id FROM permissions WHERE tenant_id=$1 AND code=$2",
        ids["tenant"],
        code,
    )
    if permission_id is None:
        permission_id = uuid7()
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
            "VALUES($1,$2,$3,'U08C acceptance permission',now(),now())",
            permission_id,
            ids["tenant"],
            code,
        )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        ids["tenant"],
        role_id or ids["admin_role"],
        permission_id,
    )


async def _session(
    owner,
    ids,
    *,
    revoked: bool = False,
    expires_at: datetime | None = None,
    auth_version: int = 0,
    account_id: uuid.UUID | None = None,
) -> uuid.UUID:
    session_id = uuid7()
    await owner.execute(
        "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,revoked_at,"
        "created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,now(),now())",
        session_id,
        account_id or ids["account"],
        ids["tenant"],
        auth_version,
        uuid.uuid4().hex,
        expires_at or datetime.now(UTC) + timedelta(hours=1),
        datetime.now(UTC) if revoked else None,
    )
    return session_id


async def _call(
    runtime,
    ids,
    session_id,
    *,
    export_id=None,
    export_type="scan_stats_xlsx",
    reason="monthly operating review",
    idem="u08c-1",
    scope='{"date_from":"2026-08-01","date_to":"2026-08-14"}',
    file_name="scan-stats.xlsx",
    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    row_count=12,
    checksum=None,
    file_size=17,
    resource_id=None,
    code_batch_id=None,
    manifest_version=None,
    artifact_ciphertext=None,
    artifact_nonce=None,
    artifact_scheme=None,
    artifact_key_id=None,
):
    checksum = checksum or hashlib.sha256(b"prepared workbook").hexdigest()
    return await runtime.fetchrow(
        CALL,
        ids["tenant"],
        session_id,
        export_id or uuid7(),
        export_type,
        reason,
        scope,
        idem,
        file_name,
        content_type,
        row_count,
        checksum,
        file_size,
        resource_id,
        code_batch_id,
        manifest_version,
        artifact_ciphertext,
        artifact_nonce,
        artifact_scheme,
        artifact_key_id,
    )


async def _add_principal(owner, ids, role_name: str) -> tuple[uuid.UUID, uuid.UUID]:
    account_id = uuid7()
    role_id = uuid7()
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        account_id,
        ids["tenant"],
        ids["organization"],
        f"u08c-{account_id.hex}@test.local",
        f"u08c {role_name}",
    )
    await owner.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        role_id,
        ids["tenant"],
        role_name,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        ids["tenant"],
        account_id,
        role_id,
    )
    return account_id, role_id


async def _export_rls_catalog_snapshot(owner) -> tuple[bool, bool, list[tuple]]:
    relation = await owner.fetchrow(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.export_logs'::regclass"
    )
    policies = await owner.fetch(
        "SELECT polname,polpermissive,polroles::text,polcmd,"
        "pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) "
        "FROM pg_policy WHERE polrelid='public.export_logs'::regclass ORDER BY polname"
    )
    return relation["relrowsecurity"], relation["relforcerowsecurity"], [tuple(row) for row in policies]


async def _purge_owner_seed_fixture(
    owner: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
) -> None:
    """Delete the marker-owned seed manifest graph and prove no fact remains."""

    if owner.is_in_transaction():
        await owner.execute("ROLLBACK")
    await _purge_owned_delivery_fixture(owner, ids["tenant"])
    consent_tables = (
        "consumer_consent_actions",
        "consent_records",
        "consumer_consent_policy_current",
        "consumer_consent_policies",
    )
    try:
        async with owner.transaction():
            for table in consent_tables:
                await owner.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER USER")
            for table in consent_tables:
                await owner.execute(f"DELETE FROM public.{table} WHERE tenant_id=$1", ids["tenant"])
            for table in reversed(consent_tables):
                await owner.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
    except BaseException:
        async with owner.transaction():
            for table in reversed(consent_tables):
                await owner.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
        raise
    identity_tables = (
        "account_roles",
        "role_permissions",
        "permissions",
        "accounts",
        "roles",
        "organizations",
        "tenants",
    )
    try:
        async with owner.transaction():
            for table in identity_tables:
                await owner.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER USER")
            await owner.execute("DELETE FROM production_batches WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM skus WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM products WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM brands WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM auth_sessions WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM role_permissions WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM account_roles WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM permissions WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM accounts WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM roles WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM organizations WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM tenants WHERE id=$1", ids["tenant"])
            for table in reversed(identity_tables):
                await owner.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
    except BaseException:
        async with owner.transaction():
            for table in reversed(identity_tables):
                await owner.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
        raise

    tenant_tables = (
        "export_logs",
        "code_items",
        "code_batches",
        "code_batch_generation_receipts",
        "production_batches",
        "skus",
        "products",
        "brands",
        "auth_sessions",
        "role_permissions",
        "account_roles",
        "permissions",
        "accounts",
        "roles",
        "organizations",
        *consent_tables,
    )
    for table in tenant_tables:
        assert await owner.fetchval(f"SELECT count(*) FROM public.{table} WHERE tenant_id=$1", ids["tenant"]) == 0
    assert await owner.fetchval("SELECT count(*) FROM tenants WHERE id=$1", ids["tenant"]) == 0


async def test_export_ledger_authority_is_actor_bound_immutable_and_idempotent(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        ids = await _seed_catalog(owner, "u08c-ledger")
        await _grant_permission(owner, ids, "analytics:view")
        session_id = await _session(owner, ids)
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))

        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await runtime.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.export_logs',$1)", privilege
            )
        assert await runtime.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            "public.record_prepared_export(uuid,uuid,uuid,text,text,jsonb,text,text,text,integer,text,bigint,"
            "uuid,uuid,integer,bytea,bytea,text,text)",
        )
        assert not await runtime.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            "public.record_seed_code_export_manifest(uuid,uuid,uuid,text,integer,text,bigint,bytea,bytea,text,text)",
        )

        first = await _call(runtime, ids, session_id)
        replay = await _call(runtime, ids, session_id, export_id=uuid7())
        assert first["export_id"] == replay["export_id"]
        assert first["account_id"] == ids["account"]
        assert first["status"] == "prepared"
        assert first["replayed"] is False and replay["replayed"] is True
        row = await owner.fetchrow("SELECT * FROM export_logs WHERE id=$1", first["export_id"])
        assert row["auth_session_id"] == session_id
        assert row["authority_version"] == 1
        assert row["reason"] == "monthly operating review"

        with pytest.raises(asyncpg.UniqueViolationError):
            await _call(runtime, ids, session_id, reason="different reason")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute("UPDATE export_logs SET reason='forged' WHERE id=$1", first["export_id"])

        revoked_session = await _session(owner, ids, revoked=True)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _call(runtime, ids, revoked_session, idem="revoked")
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND idempotency_key='revoked'", ids["tenant"]
            )
            == 0
        )
    finally:
        await runtime.close()
        await owner.close()


async def test_login_prune_retains_expired_authority_sessions_and_cleans_only_unreferenced(
    migrated_pg_url: str,
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    other_runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    control = await asyncpg.connect(_control_dsn(migrated_pg_url))
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    engine = create_async_engine(control_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        ids = await _seed_catalog(owner, "u08c-retention")
        other = await _seed_catalog(owner, "u08c-retention-other")
        await _grant_permission(owner, ids, "analytics:view")
        await _grant_permission(owner, other, "analytics:view")
        retained_session = await _session(owner, ids)
        retained_other_session = await _session(owner, other)
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        export = await _call(runtime, ids, retained_session, idem="u08c-retention-proof")
        await other_runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(other["tenant"]))
        other_export = await _call(
            other_runtime,
            other,
            retained_other_session,
            idem="u08c-retention-other-proof",
        )

        assert await runtime.fetchval("SELECT count(*) FROM export_logs") == 1
        assert not await runtime.fetchval("SELECT has_parameter_privilege(session_user,'app.bypass_rls','SET')")
        # PostgreSQL accepts arbitrary custom GUC values even without a
        # parameter ACL grant. The policy's independent SET-privilege check is
        # what makes a forged value inert for the ordinary runtime role.
        await runtime.execute("SET app.bypass_rls='true'")
        assert await runtime.fetchval("SELECT count(*) FROM export_logs") == 1

        assert not await control.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=session_user")
        assert await control.fetchval("SELECT has_parameter_privilege(session_user,'app.bypass_rls','SET')")
        assert await control.fetchval("SELECT count(*) FROM export_logs") == 0
        await control.execute("SELECT set_config('app.tenant_id','',false)")
        await control.execute("SELECT set_config('app.bypass_rls','true',false)")
        assert (
            await control.fetchval(
                "SELECT count(*) FROM export_logs WHERE id=ANY($1::uuid[])",
                [export["export_id"], other_export["export_id"]],
            )
            == 2
        )
        gmv_policy = await control.fetchrow(
            "SELECT pg_get_expr(polqual,polrelid) AS using_expr,"
            "pg_get_expr(polwithcheck,polrelid) AS check_expr "
            "FROM pg_policy WHERE polrelid='public.gmv_attribution_confirmations'::regclass "
            "AND polname='tenant_isolation'"
        )
        assert "app.bypass_rls" in gmv_policy["using_expr"]
        assert "has_parameter_privilege" in gmv_policy["using_expr"]
        assert "app.bypass_rls" in gmv_policy["check_expr"]

        unreferenced_same_tenant = await _session(
            owner,
            ids,
            expires_at=datetime.now(UTC) - timedelta(hours=2),
        )
        unreferenced_other_tenant = await _session(
            owner,
            other,
            expires_at=datetime.now(UTC) - timedelta(hours=2),
        )
        await owner.execute(
            "UPDATE auth_sessions SET expires_at=$1 WHERE id=ANY($2::uuid[])",
            datetime.now(UTC) - timedelta(hours=1),
            [retained_session, retained_other_session],
        )
        password = "RetentionPassword1"
        email = f"delivery-{ids['account'].hex[:8]}@test.local"
        await owner.execute(
            "UPDATE accounts SET hashed_password=$2 WHERE tenant_id=$1 AND id=$3",
            ids["tenant"],
            hash_password(password),
            ids["account"],
        )

        async with factory() as db:
            await db.execute(text("SELECT set_config('app.tenant_id','',true)"))
            await db.execute(text("SELECT set_config('app.bypass_rls','true',true)"))
            token_pair = await authenticate_login(
                db,
                email=email,
                password=password,
                tenant_slug=None,
                client_ip="127.0.0.1",
                cache=_AllowLoginCache(),
            )
        assert token_pair["access_token"]
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM auth_sessions WHERE tenant_id=$1 AND id=$2)",
            ids["tenant"],
            retained_session,
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM auth_sessions WHERE tenant_id=$1 AND id=$2)",
            other["tenant"],
            retained_other_session,
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM export_logs WHERE tenant_id=$1 AND id=$2 AND auth_session_id=$3)",
            other["tenant"],
            other_export["export_id"],
            retained_other_session,
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM export_logs WHERE tenant_id=$1 AND id=$2 AND auth_session_id=$3)",
            ids["tenant"],
            export["export_id"],
            retained_session,
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM auth_sessions WHERE tenant_id=$1 AND id=$2)",
            ids["tenant"],
            unreferenced_same_tenant,
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM auth_sessions WHERE tenant_id=$1 AND id=$2)",
            other["tenant"],
            unreferenced_other_tenant,
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2",
                ids["tenant"],
                ids["account"],
            )
            == 2
        )
    finally:
        await engine.dispose()
        await control.close()
        await other_runtime.close()
        await runtime.close()
        await owner.close()


async def test_export_authority_rejects_missing_cross_tenant_expired_and_stale_sessions(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        ids = await _seed_catalog(owner, "u08c-negative")
        other = await _seed_catalog(owner, "u08c-cross")
        await _grant_permission(owner, ids, "analytics:view")
        await _grant_permission(owner, other, "analytics:view")
        await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", other["tenant"])
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        candidates = (
            ("missing", uuid7(), asyncpg.ForeignKeyViolationError),
            (
                "expired",
                await _session(owner, ids, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
                asyncpg.InsufficientPrivilegeError,
            ),
            ("stale", await _session(owner, ids, auth_version=7), asyncpg.InsufficientPrivilegeError),
            ("foreign", await _session(owner, other), asyncpg.ForeignKeyViolationError),
        )
        for label, session_id, error in candidates:
            with pytest.raises(error):
                await _call(runtime, ids, session_id, idem=label)
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND idempotency_key=$2",
                    ids["tenant"],
                    label,
                )
                == 0
            )
    finally:
        await runtime.close()
        await owner.close()


async def test_export_authority_serializes_exact_replay_and_conflict(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtimes = [await asyncpg.connect(_runtime_dsn(migrated_pg_url)) for _ in range(2)]
    try:
        ids = await _seed_catalog(owner, "u08c-concurrency")
        await _grant_permission(owner, ids, "analytics:view")
        session_id = await _session(owner, ids)
        for runtime in runtimes:
            await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))

        exact = await asyncio.gather(
            *(_call(runtime, ids, session_id, export_id=uuid7(), idem="u08c-concurrent-exact") for runtime in runtimes)
        )
        assert exact[0]["export_id"] == exact[1]["export_id"]
        assert sorted(row["replayed"] for row in exact) == [False, True]

        conflict = await asyncio.gather(
            _call(
                runtimes[0],
                ids,
                session_id,
                export_id=uuid7(),
                idem="u08c-concurrent-conflict",
                reason="first legitimate purpose",
            ),
            _call(
                runtimes[1],
                ids,
                session_id,
                export_id=uuid7(),
                idem="u08c-concurrent-conflict",
                reason="different legitimate purpose",
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, asyncpg.UniqueViolationError) for result in conflict) == 1
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND idempotency_key=$2",
                ids["tenant"],
                "u08c-concurrent-conflict",
            )
            == 1
        )
    finally:
        for runtime in runtimes:
            await runtime.close()
        await owner.close()


async def test_owner_seed_capability_creates_replayable_manifest_but_runtime_cannot_call(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    control = await asyncpg.connect(_control_dsn(migrated_pg_url))
    ids: dict[str, uuid.UUID] | None = None
    try:
        ids = await _seed_catalog(owner, "u08c-seed")
        receipt = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt, quantity=1, expected_item_count=1)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
        plaintext = b"public_id,status\nDEMO,created\n"
        checksum = hashlib.sha256(plaintext).hexdigest()
        ciphertext = b"c" * (len(plaintext) + 16)
        args = (
            ids["tenant"],
            uuid7(),
            batch_id,
            "codes.csv",
            1,
            checksum,
            len(plaintext),
            ciphertext,
            b"n" * 12,
            "aes-256-gcm-v1",
            "test-key-1",
        )
        seed_call = "SELECT * FROM public.record_seed_code_export_manifest($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.tenant_id','',true)")
            await owner.execute("SELECT set_config('app.bypass_rls','true',true)")
            first = await owner.fetchrow(seed_call, *args)
            replay = await owner.fetchrow(seed_call, args[0], uuid7(), *args[2:])
        assert first["export_id"] == replay["export_id"]
        assert first["replayed"] is False and replay["replayed"] is True
        row = await owner.fetchrow("SELECT * FROM export_logs WHERE id=$1", first["export_id"])
        assert row["authority_version"] == 2
        assert row["auth_session_id"] is None
        assert row["account_id"] == ids["account"]
        assert row["reason"] == "trusted seed prepared code artifact"
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(seed_call, args[0], uuid7(), *args[2:])
        async with control.transaction():
            await control.execute("SELECT set_config('app.tenant_id','',true)")
            await control.execute("SELECT set_config('app.bypass_rls','true',true)")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await control.fetchrow(seed_call, args[0], uuid7(), *args[2:])
    finally:
        await control.close()
        await runtime.close()
        if ids is not None:
            await _purge_owner_seed_fixture(owner, ids)
        await owner.close()


async def test_export_authority_matches_exact_type_role_mime_and_row_limit_contract(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        ids = await _seed_catalog(owner, "u08c-matrix")
        for permission in ("analytics:view", "risk:read", "code:export", "takeover:prepare"):
            await _grant_permission(owner, ids, permission)
        admin_session = await _session(owner, ids)
        operator_id, operator_role = await _add_principal(owner, ids, "operator")
        await _grant_permission(owner, ids, "analytics:view", role_id=operator_role)
        await _grant_permission(owner, ids, "code:export", role_id=operator_role)
        operator_session = await _session(owner, ids, account_id=operator_id)
        viewer_id, viewer_role = await _add_principal(owner, ids, "viewer")
        await _grant_permission(owner, ids, "code:export", role_id=viewer_role)
        viewer_session = await _session(owner, ids, account_id=viewer_id)
        receipt = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt, quantity=1, expected_item_count=1)
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))

        analytics_types = (
            "scan_events_xlsx",
            "scan_stats_xlsx",
            "campaign_dashboard_xlsx",
            "regional_dashboard_xlsx",
        )
        for export_type in analytics_types:
            row = await _call(
                runtime,
                ids,
                admin_session,
                export_type=export_type,
                idem=f"matrix-{export_type}",
                row_count=50_000,
            )
            assert row["row_count"] == 50_000

        for export_type, content_type, file_name in (
            (
                "risk_dashboard_xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "risk-dashboard.xlsx",
            ),
            ("risk_alerts_csv", "text/csv; charset=utf-8", "risk-alerts.csv"),
            ("risk_diversions_csv", "text/csv; charset=utf-8", "risk-diversions.csv"),
        ):
            assert await _call(
                runtime,
                ids,
                admin_session,
                export_type=export_type,
                idem=f"matrix-{export_type}",
                content_type=content_type,
                file_name=file_name,
            )

        assert await _call(
            runtime,
            ids,
            operator_session,
            export_type="code_csv_download",
            idem="matrix-code-download",
            file_name="codes.csv",
            content_type="text/csv; charset=utf-8",
            resource_id=batch_id,
            code_batch_id=batch_id,
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _call(
                runtime,
                ids,
                operator_session,
                export_type="takeover_import_errors_csv",
                idem="matrix-takeover-missing-permission",
                file_name="takeover-errors.csv",
                content_type="text/csv; charset=utf-8",
            )
        await _grant_permission(owner, ids, "takeover:prepare", role_id=operator_role)
        assert await _call(
            runtime,
            ids,
            operator_session,
            export_type="takeover_import_errors_csv",
            idem="matrix-takeover",
            file_name="takeover-errors.csv",
            content_type="text/csv; charset=utf-8",
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _call(runtime, ids, operator_session, idem="matrix-operator-analytics")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _call(
                runtime,
                ids,
                viewer_session,
                export_type="code_csv_download",
                idem="matrix-viewer-code",
                file_name="codes.csv",
                content_type="text/csv; charset=utf-8",
                resource_id=batch_id,
                code_batch_id=batch_id,
            )
        with pytest.raises(asyncpg.DataError):
            await _call(runtime, ids, admin_session, idem="matrix-too-many", row_count=50_001)
        with pytest.raises(asyncpg.DataError):
            await _call(
                runtime,
                ids,
                admin_session,
                export_type="risk_alerts_csv",
                idem="matrix-noncanonical-mime",
                file_name="risk-alerts.csv",
                content_type="text/csv",
            )
    finally:
        await runtime.close()
        await owner.close()


async def test_code_manifest_idempotency_binds_complete_encryption_envelope(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        ids = await _seed_catalog(owner, "u08c-envelope")
        await _grant_permission(owner, ids, "code:export")
        session_id = await _session(owner, ids)
        receipt = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt, quantity=1, expected_item_count=1)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
        plaintext = b"public_id,status\nDEMO,created\n"
        checksum = hashlib.sha256(plaintext).hexdigest()
        ciphertext = b"c" * (len(plaintext) + 16)
        kwargs = {
            "export_type": "code_csv",
            "idem": "u08c-code-envelope",
            "scope": '{"manifest_version":1}',
            "file_name": "codes.csv",
            "content_type": "text/csv; charset=utf-8",
            "row_count": 1,
            "checksum": checksum,
            "file_size": len(plaintext),
            "resource_id": batch_id,
            "code_batch_id": batch_id,
            "manifest_version": 1,
            "artifact_ciphertext": ciphertext,
            "artifact_nonce": b"n" * 12,
            "artifact_scheme": "aes-256-gcm-v1",
            "artifact_key_id": "test-key-1",
        }
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        first = await _call(runtime, ids, session_id, **kwargs)
        replay = await _call(runtime, ids, session_id, export_id=uuid7(), **kwargs)
        assert first["export_id"] == replay["export_id"] and replay["replayed"] is True
        for changed in (
            {"artifact_nonce": b"m" * 12},
            {"artifact_key_id": "test-key-2"},
            {"artifact_ciphertext": b"d" * (len(plaintext) + 16)},
        ):
            with pytest.raises(asyncpg.UniqueViolationError):
                await _call(runtime, ids, session_id, export_id=uuid7(), **(kwargs | changed))
    finally:
        await runtime.close()
        await owner.close()


async def test_staged_populated_cutover_cic_retry_downgrade_and_fact_guard(migrated_pg_url: str) -> None:
    database_name = f"yimatong_acceptance_u8c_stage_{uuid.uuid4().hex[:10]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    owner = None
    locker = None
    runtime = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8b2e3f4a5b6")
        owner = await asyncpg.connect(_owner_dsn(database_url))
        ids = await _seed_catalog(owner, "u08c-stage")

        async def insert_legacy(export_id: uuid.UUID) -> None:
            await owner.execute(
                "INSERT INTO export_logs(id,tenant_id,account_id,export_type,file_name,row_count,status,created_at,updated_at) "
                "VALUES($1,$2,$3,'scan_stats_xlsx','legacy.xlsx',7,'completed',now(),now())",
                export_id,
                ids["tenant"],
                ids["account"],
            )

        first_legacy = uuid7()
        await insert_legacy(first_legacy)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8c0f1a2b3c4")
        assert await owner.fetchval(
            "SELECT is_nullable='YES' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='export_logs' AND column_name='reason'"
        )
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8c1a2b3c4d5")
        coexistence_legacy = uuid7()
        await insert_legacy(coexistence_legacy)

        locker = await asyncpg.connect(_owner_dsn(database_url))
        lock_tx = locker.transaction()
        await lock_tx.start()
        await locker.execute("LOCK TABLE export_logs IN ACCESS EXCLUSIVE MODE")
        failed = await asyncio.to_thread(
            _alembic,
            database_url,
            "upgrade",
            "u8c2b3c4d5e6",
            succeeds=False,
        )
        assert "lock timeout" in (failed.stdout + failed.stderr).lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8c1a2b3c4d5"
        await lock_tx.rollback()
        await locker.close()
        locker = None

        # A real old-shape writer can stay open while CIC starts. Releasing it
        # lets the online index complete without losing the committed row.
        locker = await asyncpg.connect(_owner_dsn(database_url))
        writer_tx = locker.transaction()
        await writer_tx.start()
        concurrent_legacy = uuid7()
        await locker.execute(
            "INSERT INTO export_logs(id,tenant_id,account_id,export_type,file_name,row_count,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'scan_events_xlsx','concurrent.xlsx',9,'completed',now(),now())",
            concurrent_legacy,
            ids["tenant"],
            ids["account"],
        )
        cic = asyncio.create_task(asyncio.to_thread(_alembic, database_url, "upgrade", "u8c2b3c4d5e6"))
        await asyncio.sleep(0.25)
        await writer_tx.commit()
        await locker.close()
        locker = None
        await cic
        parent_rls_snapshot = await _export_rls_catalog_snapshot(owner)
        assert parent_rls_snapshot[0:2] == (True, True)

        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        assert await owner.fetchval(
            "SELECT count(*)=3 FROM export_logs WHERE id=ANY($1::uuid[]) AND authority_version=0 "
            "AND auth_session_id IS NULL AND reason='legacy export record; original reason unavailable'",
            [first_legacy, coexistence_legacy, concurrent_legacy],
        )
        assert await owner.fetchval(
            "SELECT bool_and(convalidated) FROM pg_constraint WHERE conname=ANY($1::text[])",
            [
                "ck_export_logs_authority_version_u8c",
                "ck_export_logs_authoritative_shape_u8c",
                "fk_export_logs_tenant_account_u8c",
                "fk_export_logs_tenant_auth_session_u8c",
            ],
        )
        assert await owner.fetchval(
            "SELECT indisvalid AND indisready FROM pg_index "
            "WHERE indexrelid='public.uq_export_logs_tenant_idempotency_u8c'::regclass"
        )
        assert await owner.fetchval(
            "SELECT bool_and(indisvalid AND indisready) FROM pg_index WHERE indexrelid=ANY($1::regclass[])",
            [
                "public.ix_export_logs_tenant_auth_session_u8c",
                "public.ix_gmv_attr_confirmations_tenant_auth_session_u8c",
            ],
        )
        assert await owner.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE oid='public.export_logs'::regclass")
        assert await owner.fetchval(
            "SELECT pg_get_expr(polqual,polrelid) LIKE '%app.bypass_rls%' "
            "AND pg_get_expr(polwithcheck,polrelid) LIKE '%app.bypass_rls%' "
            "FROM pg_policy WHERE polrelid='public.export_logs'::regclass "
            "AND polname='export_logs_tenant_isolation'"
        )

        await asyncio.to_thread(_alembic, database_url, "downgrade", "u8c2b3c4d5e6")
        assert await _export_rls_catalog_snapshot(owner) == parent_rls_snapshot
        assert await owner.fetchval(
            "SELECT to_regprocedure('public.record_prepared_export(uuid,uuid,uuid,text,text,jsonb,text,text,text,"
            "integer,text,bigint,uuid,uuid,integer,bytea,bytea,text,text)') IS NULL"
        )
        assert await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.export_logs','INSERT') "
            "AND has_table_privilege('yimatong_app','public.export_logs','UPDATE')"
        )
        assert not await owner.fetchval(
            "SELECT has_column_privilege('yimatong_app','public.export_logs','artifact_ciphertext','SELECT')"
        )
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
        await _grant_permission(owner, ids, "analytics:view")
        session_id = await _session(owner, ids)
        runtime = await asyncpg.connect(_runtime_dsn(database_url))
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        await _call(runtime, ids, session_id, idem="u08c-downgrade-fact")
        blocked = await asyncio.to_thread(
            _alembic,
            database_url,
            "downgrade",
            "u8c2b3c4d5e6",
            succeeds=False,
        )
        assert "immutable prepared export facts exist" in (blocked.stdout + blocked.stderr)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8c4d5e6f7a8"
        await asyncio.to_thread(_alembic, database_url, "check")
    finally:
        if runtime is not None:
            await runtime.close()
        if locker is not None:
            await locker.close()
        if owner is not None:
            await owner.close()
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=not lease.marker_written,
            )
