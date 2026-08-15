"""Real PostgreSQL contract for the immutable external-order net-value ledger."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from uuid6 import uuid7

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic
from tests.test_acceptance.test_consumer_consent_authority import _owner_dsn, _runtime_dsn

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

CALL = """SELECT * FROM record_external_order_value_event(
 $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)"""
FUNCTION_SIGNATURE = (
    "record_external_order_value_event(uuid,text,text,text,numeric,text,text,text,text,uuid,"
    "timestamp with time zone,timestamp with time zone,text,text,text,text)"
)
U8A_INDEXES = (
    "uq_external_orders_tenant_id_id_u8a",
    "uq_external_order_receipts_idem_u8a",
    "uq_external_order_receipts_tenant_id_u8a",
    "uq_external_order_events_sequence_u8a",
    "uq_external_order_events_tenant_id_u8a",
    "ix_external_order_events_identity_u8a",
)

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _sole_current_head_with_u8a_ancestor() -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = tuple(script.get_heads())
    assert len(heads) == 1, f"expected one repository head, got {heads!r}"
    ancestry = {revision.revision for revision in script.iterate_revisions(heads[0], "base")}
    assert "u8a3f4a5b6c7" in ancestry
    return heads[0]


async def _u8a_head_snapshot(owner: asyncpg.Connection, tenant_id: uuid.UUID) -> tuple[str, str, str, str]:
    head = await owner.fetchval("SELECT version_num FROM alembic_version")
    catalog_digest = await owner.fetchval(
        "SELECT md5(COALESCE(string_agg(item,E'\\n' ORDER BY item),'')) FROM ("
        "SELECT 'column:'||table_name||':'||column_name||':'||data_type||':'||is_nullable||':'||"
        "COALESCE(column_default,'') item FROM information_schema.columns WHERE table_schema='public' UNION ALL "
        "SELECT 'relation:'||cls.relname||':'||cls.relkind::text||':'||cls.relrowsecurity||':'||"
        "cls.relforcerowsecurity||':'||"
        "COALESCE(cls.relacl::text,'') FROM pg_class cls JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
        "WHERE ns.nspname='public' UNION ALL "
        "SELECT 'constraint:'||constraint_row.conname||':'||pg_get_constraintdef(constraint_row.oid) "
        "FROM pg_constraint constraint_row JOIN pg_class cls ON cls.oid=constraint_row.conrelid "
        "JOIN pg_namespace ns ON ns.oid=cls.relnamespace WHERE ns.nspname='public' UNION ALL "
        "SELECT 'index:'||index_row.indexrelid::regclass::text||':'||pg_get_indexdef(index_row.indexrelid) "
        "FROM pg_index index_row JOIN pg_class cls ON cls.oid=index_row.indrelid "
        "JOIN pg_namespace ns ON ns.oid=cls.relnamespace WHERE ns.nspname='public' UNION ALL "
        "SELECT 'trigger:'||trigger_row.tgname||':'||pg_get_triggerdef(trigger_row.oid) FROM pg_trigger trigger_row "
        "JOIN pg_class cls ON cls.oid=trigger_row.tgrelid JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
        "WHERE ns.nspname='public' AND NOT trigger_row.tgisinternal UNION ALL "
        "SELECT 'policy:'||policy_row.polname||':'||pg_get_expr(policy_row.polqual,policy_row.polrelid)||':'||"
        "COALESCE(pg_get_expr(policy_row.polwithcheck,policy_row.polrelid),'') FROM pg_policy policy_row "
        "JOIN pg_class cls ON cls.oid=policy_row.polrelid JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
        "WHERE ns.nspname='public' UNION ALL "
        "SELECT 'function:'||procedure_row.oid::regprocedure::text||':'||pg_get_functiondef(procedure_row.oid)||':'||"
        "COALESCE(procedure_row.proacl::text,'') FROM pg_proc procedure_row "
        "JOIN pg_namespace ns ON ns.oid=procedure_row.pronamespace WHERE ns.nspname='public'"
        ") snapshot_items"
    )
    fact_digest = await owner.fetchval(
        "SELECT md5(COALESCE(string_agg(item,E'\\n' ORDER BY item),'')) FROM ("
        "SELECT 'order:'||to_jsonb(fact_row)::text item FROM external_orders fact_row WHERE tenant_id=$1 UNION ALL "
        "SELECT 'event:'||to_jsonb(fact_row)::text FROM external_order_value_events fact_row WHERE tenant_id=$1 "
        "UNION ALL SELECT 'receipt:'||to_jsonb(fact_row)::text FROM external_order_value_receipts fact_row "
        "WHERE tenant_id=$1 UNION ALL SELECT 'marker:'||to_jsonb(fact_row)::text "
        "FROM external_order_ledger_recovery_markers fact_row WHERE tenant_id=$1"
        ") fact_items",
        tenant_id,
    )
    xmin_digest = await owner.fetchval(
        "SELECT md5(COALESCE(string_agg(item,E'\\n' ORDER BY item),'')) FROM ("
        "SELECT 'order:'||id::text||':'||xmin::text item FROM external_orders WHERE tenant_id=$1 UNION ALL "
        "SELECT 'event:'||id::text||':'||xmin::text FROM external_order_value_events WHERE tenant_id=$1 UNION ALL "
        "SELECT 'receipt:'||id::text||':'||xmin::text FROM external_order_value_receipts WHERE tenant_id=$1 UNION ALL "
        "SELECT 'marker:'||id::text||':'||xmin::text FROM external_order_ledger_recovery_markers WHERE tenant_id=$1"
        ") xmin_items",
        tenant_id,
    )
    return head, catalog_digest, fact_digest, xmin_digest


@pytest_asyncio.fixture
async def isolated_u8a_populated_pg(migrated_pg_url: str):
    database_name = f"yimatong_acceptance_u8a_perm_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8a1d2e3f4a5")
        yield database_url, lease
    finally:
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=lease.created and not lease.marker_written,
            )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _seed_actor(owner: asyncpg.Connection, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, organization_id, actor_id, role_id = uuid7(), uuid7(), uuid7(), uuid7()
    transaction = owner.transaction()
    await transaction.start()
    await owner.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        tenant_id,
        label,
        f"{label}-{tenant_id.hex[:8]}",
    )
    await owner.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        organization_id,
        tenant_id,
        label,
    )
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,is_active,"
        "auth_version,created_at,updated_at) VALUES($1,$2,$3,$4,'x','ledger actor',0,true,1,now(),now())",
        actor_id,
        tenant_id,
        organization_id,
        f"{label}@example.test",
    )
    await owner.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
        role_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        tenant_id,
        actor_id,
        role_id,
    )
    await owner.execute(
        "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,"
        "created_at,updated_at) VALUES($1,$2,$3,1,$4,now()+interval '1 hour',now(),now())",
        uuid7(),
        tenant_id,
        actor_id,
        uuid.uuid4().hex,
    )
    if await owner.fetchval("SELECT to_regclass('public.external_order_permission_backfill') IS NOT NULL"):
        permission_id = uuid7()
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
            "VALUES($1,$2,'order:manage','test order authority',now(),now())",
            permission_id,
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            tenant_id,
            role_id,
            permission_id,
        )
    await transaction.commit()
    return tenant_id, actor_id


async def _call(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    event_type: str,
    amount: Decimal | None,
    idem: str,
    payload: str,
) -> asyncpg.Record:
    return await conn.fetchrow(
        CALL,
        tenant_id,
        "erp",
        "ORDER-1",
        event_type,
        amount,
        "CNY",
        idem,
        _digest(payload),
        _digest(f"source:{payload}"),
        auth_session_id,
        datetime.now(UTC),
        datetime.now(UTC) if event_type == "order_confirmed" else None,
        None,
        "SKU A",
        "tmall",
        "accepted test event",
    )


async def test_00_repair3_fresh_pg_failure_cleanup_quarantine_and_post_head_role(
    migrated_pg_url: str,
) -> None:
    current_head = _sole_current_head_with_u8a_ancestor()
    database_name = f"yimatong_acceptance_u8a3_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    owner: asyncpg.Connection | None = None
    locker: asyncpg.Connection | None = None
    observer: asyncpg.Connection | None = None
    runtime: asyncpg.Connection | None = None
    primary_error: BaseException | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8a0c1d2e3f4")
        owner = await asyncpg.connect(_owner_dsn(database_url))
        tenant_id, valid_order_id = uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'U08A Repair3','u8a-repair3','active','free','brand',now(),now())",
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
            "currency,created_at,updated_at) VALUES($1,$2,'VALID-1',10,true,'erp','paid',0,'CNY',now(),now())",
            valid_order_id,
            tenant_id,
        )

        # Hold a legacy writer. Its shared transaction coordination lock makes
        # the builder fail before the first CIC can publish a catalog shell.
        locker = await asyncpg.connect(_owner_dsn(database_url))
        observer = await asyncpg.connect(_owner_dsn(database_url))

        # A writer starting after the builder owns the target coordination key
        # waits in the u8a0 trigger, before it can interfere with CIC.
        assert await observer.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended('u8a:external_orders:index-build',0))"
        )
        locker_pid = await locker.fetchval("SELECT pg_backend_pid()")
        late_writer = asyncio.create_task(
            locker.execute("UPDATE external_orders SET product_name='late-writer' WHERE id=$1", valid_order_id)
        )
        for _ in range(100):
            if await owner.fetchval("SELECT wait_event='AdvisoryLock' FROM pg_stat_activity WHERE pid=$1", locker_pid):
                break
            await asyncio.sleep(0.02)
        assert not late_writer.done()
        assert await owner.fetchval("SELECT product_name IS NULL FROM external_orders WHERE id=$1", valid_order_id)
        assert await observer.fetchval(
            "SELECT pg_advisory_unlock(hashtextextended('u8a:external_orders:index-build',0))"
        )
        await asyncio.wait_for(late_writer, timeout=2)
        assert await owner.fetchval(
            "SELECT product_name='late-writer' FROM external_orders WHERE id=$1", valid_order_id
        )

        blocking = locker.transaction()
        await blocking.start()
        await locker.execute("UPDATE external_orders SET updated_at=updated_at WHERE id=$1", valid_order_id)
        failed_upgrade = asyncio.create_task(
            asyncio.to_thread(_alembic, database_url, "upgrade", "u8a1d2e3f4a5", succeeds=False)
        )
        try:
            await asyncio.wait_for(asyncio.shield(failed_upgrade), timeout=15)
        except TimeoutError:
            await blocking.rollback()
            await failed_upgrade
            pytest.fail("u8a1 relation coordination was not bounded while a legacy writer was held")
        finally:
            if locker.is_in_transaction():
                await blocking.rollback()

        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8a0c1d2e3f4"
        assert await owner.fetchval("SELECT count(*) FROM external_orders") == 1
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM external_order_value_receipts "
            "UNION ALL SELECT 1 FROM external_order_value_events)"
        )
        assert await owner.fetchval("SELECT to_regprocedure($1) IS NULL", f"public.{FUNCTION_SIGNATURE}")
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "WHERE c.relname=ANY($1::text[]))",
            list(U8A_INDEXES),
        )
        # An unrelated write transaction does not take the target relation key
        # and therefore cannot be rejected by the online index phase.
        unrelated = locker.transaction()
        await unrelated.start()
        await locker.execute("UPDATE tenants SET name='U08A unrelated writer' WHERE id=$1", tenant_id)
        await asyncio.wait_for(asyncio.to_thread(_alembic, database_url, "upgrade", "u8a1d2e3f4a5"), timeout=15)
        await unrelated.commit()
        assert await owner.fetchval(
            "SELECT bool_and(i.indisvalid AND i.indisready AND i.indislive) FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=ANY($1::text[])",
            list(U8A_INDEXES),
        )

        # u8a2 freezes legacy writes before its first classification scan. A
        # concurrent insert makes the cutover fail atomically; once committed,
        # retry must include the new legacy order in the authoritative backfill.
        late_order_id = uuid7()
        cutover_writer = locker.transaction()
        await cutover_writer.start()
        await locker.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
            "currency,created_at,updated_at) VALUES($1,$2,'LATE-LEGACY',15,false,'erp','paid',0,'CNY',now(),now())",
            late_order_id,
            tenant_id,
        )
        await asyncio.wait_for(
            asyncio.to_thread(_alembic, database_url, "upgrade", "u8a2e3f4a5b6", succeeds=False),
            timeout=15,
        )
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8a1d2e3f4a5"
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM external_order_value_receipts "
            "UNION ALL SELECT 1 FROM external_order_value_events "
            "UNION ALL SELECT 1 FROM external_order_ledger_recovery_markers)"
        )
        assert await owner.fetchval(
            "SELECT ledger_original_amount IS NULL AND ledger_net_amount IS NULL FROM external_orders WHERE id=$1",
            valid_order_id,
        )
        assert await owner.fetchval("SELECT to_regprocedure($1) IS NULL", f"public.{FUNCTION_SIGNATURE}")
        await cutover_writer.commit()

        invalid_rows = (
            (uuid7(), "NULL-SOURCE", 10.0, None, "paid", 0.0),
            (uuid7(), "BLANK-SOURCE", 10.0, "   ", "paid", 0.0),
            (uuid7(), "OVERFLOW", 100_000_000_000_000.0, "erp", "paid", 0.0),
            (uuid7(), "ROUND-ZERO", 0.0000004, "erp", "paid", 0.0),
            (uuid7(), "REFUND-ROUND-ZERO", 10.0, "erp", "partially_refunded", 0.0000004),
            (uuid7(), "NON-FINITE", float("inf"), "erp", "paid", 0.0),
        )
        await owner.executemany(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
            "currency,created_at,updated_at) VALUES($1,$2,$3,$4,false,$5,$6,$7,'CNY',now(),now())",
            [(row[0], tenant_id, *row[1:]) for row in invalid_rows],
        )
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == current_head

        markers = {
            row["external_id"]: row["reason"]
            for row in await owner.fetch(
                "SELECT o.external_id,m.reason FROM external_orders o "
                "JOIN external_order_ledger_recovery_markers m ON m.tenant_id=o.tenant_id AND m.order_id=o.id "
                "WHERE o.tenant_id=$1",
                tenant_id,
            )
        }
        assert markers == {
            "NULL-SOURCE": "legacy_source_identity_is_invalid",
            "BLANK-SOURCE": "legacy_source_identity_is_invalid",
            "OVERFLOW": "legacy_original_amount_is_not_representable",
            "ROUND-ZERO": "legacy_original_amount_is_not_representable",
            "REFUND-ROUND-ZERO": "legacy_refund_amount_is_not_representable",
            "NON-FINITE": "legacy_original_amount_is_not_representable",
        }
        assert await owner.fetchval(
            "SELECT bool_and(ledger_original_amount IS NULL AND ledger_refunded_amount IS NULL "
            "AND ledger_cancelled_amount IS NULL AND ledger_net_amount IS NULL AND ledger_status IS NULL) "
            "FROM external_orders WHERE id=ANY($1::uuid[])",
            [row[0] for row in invalid_rows],
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM external_order_value_receipts WHERE order_id=ANY($1::uuid[])",
                [row[0] for row in invalid_rows],
            )
            == 0
        )
        assert await owner.fetchval(
            "SELECT ledger_original_amount=10.000000 AND ledger_net_amount=10.000000 FROM external_orders WHERE id=$1",
            valid_order_id,
        )
        assert await owner.fetchval(
            "SELECT ledger_original_amount=15.000000 AND ledger_net_amount=15.000000 FROM external_orders WHERE id=$1",
            late_order_id,
        )

        qualified_signature = f"public.{FUNCTION_SIGNATURE}"
        await owner.execute(f"REVOKE ALL ON FUNCTION {qualified_signature} FROM PUBLIC, yimatong_app")
        await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            qualified_signature,
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('public',$1,'EXECUTE')",
            qualified_signature,
        )
        for table in ("external_orders", "external_order_value_events", "external_order_value_receipts"):
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
        runtime = await asyncpg.connect(_runtime_dsn(database_url))
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute("UPDATE external_orders SET ledger_net_amount=0 WHERE id=$1", valid_order_id)
        before_downgrade = await _u8a_head_snapshot(owner, tenant_id)
        blocked = await asyncio.to_thread(_alembic, database_url, "downgrade", "u8a2e3f4a5b6", succeeds=False)
        assert "immutable" in (blocked.stdout + blocked.stderr).lower()
        assert await _u8a_head_snapshot(owner, tenant_id) == before_downgrade
        assert before_downgrade[0] == current_head
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        for connection in (runtime, observer, locker, owner):
            if connection is not None and not connection.is_closed():
                await connection.close()
        if lease.created:
            try:
                await _drop_database_with_retry(
                    lease.database_name,
                    ADMIN_DSN,
                    expected_owner_marker=lease.owner_marker,
                    allow_unmarked_created=lease.created and not lease.marker_written,
                )
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"U08A Repair3 database cleanup also failed: {cleanup_error!r}")


async def test_00_populated_upgrade_registers_exact_role_permissions_and_downgrade_is_owned(
    isolated_u8a_populated_pg,
) -> None:
    migrated_pg_url, lease = isolated_u8a_populated_pg
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    tenant_id, _actor_id = await _seed_actor(owner, "u8a-permissions")
    legacy_order_id = uuid7()
    await owner.execute(
        "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
        "currency,created_at,updated_at) VALUES($1,$2,'PERMISSION-BACKFILL',25,false,'erp','paid',0,'CNY',now(),now())",
        legacy_order_id,
        tenant_id,
    )
    admin_role = await owner.fetchval("SELECT id FROM roles WHERE tenant_id=$1 AND name='admin'", tenant_id)
    role_ids = {"admin": admin_role}
    try:
        for role_name in ("operator", "viewer", "distributor"):
            role_id = uuid7()
            await owner.execute(
                "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
                role_id,
                tenant_id,
                role_name,
            )
            role_ids[role_name] = role_id
        preexisting_read = uuid7()
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
            "VALUES($1,$2,'order:read','preexisting',now(),now())",
            preexisting_read,
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            tenant_id,
            admin_role,
            preexisting_read,
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "u8a3f4a5b6c7")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        rows = await owner.fetch(
            "SELECT role.name,permission.code FROM roles role "
            "LEFT JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id "
            "LEFT JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id "
            "WHERE role.tenant_id=$1 AND (permission.code LIKE 'order:%' OR permission.code IS NULL) "
            "ORDER BY role.name,permission.code",
            tenant_id,
        )
        resolved = {(row["name"], row["code"]) for row in rows}
        assert resolved >= {
            ("admin", "order:read"),
            ("admin", "order:manage"),
            ("operator", "order:read"),
            ("operator", "order:manage"),
            ("viewer", None),
            ("distributor", None),
        }
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM permissions WHERE tenant_id=$1 AND code IN ('order:read','order:manage')",
                tenant_id,
            )
            == 2
        )
        managed_permission = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='order:manage'",
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            tenant_id,
            role_ids["viewer"],
            managed_permission,
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u8a2e3f4a5b6", succeeds=False)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8a3f4a5b6c7"
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3)",
            tenant_id,
            role_ids["viewer"],
            managed_permission,
        )
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            tenant_id,
            role_ids["viewer"],
            managed_permission,
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u8a2e3f4a5b6")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT count(*)=1 FROM external_order_value_events WHERE order_id=$1 AND provenance_type='backfill'",
            legacy_order_id,
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3)",
            tenant_id,
            admin_role,
            preexisting_read,
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM permissions WHERE tenant_id=$1 AND code='order:manage')",
            tenant_id,
        )
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u8a1d2e3f4a5")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT NOT EXISTS(SELECT 1 FROM external_order_value_events WHERE order_id=$1) "
            "AND NOT EXISTS(SELECT 1 FROM external_order_value_receipts WHERE order_id=$1)",
            legacy_order_id,
        )
        assert await owner.fetchval(
            "SELECT ledger_original_amount IS NULL AND ledger_net_amount IS NULL FROM external_orders WHERE id=$1",
            legacy_order_id,
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.external_orders',$1)", privilege
            )
        for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.external_orders',$1)", privilege
            )
        for table in (
            "external_order_value_receipts",
            "external_order_value_events",
            "external_order_ledger_recovery_markers",
        ):
            for role in ("yimatong_app", "public"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    assert not await owner.fetchval(
                        "SELECT has_table_privilege($1,'public.'||$2,$3)", role, table, privilege
                    )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('public','public.external_orders',$1)", privilege
            )
    finally:
        await owner.close()

    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    legacy_crud_id = uuid7()
    try:
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        await runtime.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
            "currency,created_at,updated_at) VALUES($1,$2,'LEGACY-CRUD',5,false,'erp','paid',0,'CNY',now(),now())",
            legacy_crud_id,
            tenant_id,
        )
        assert await runtime.fetchval(
            "SELECT amount=5 FROM external_orders WHERE tenant_id=$1 AND id=$2", tenant_id, legacy_crud_id
        )
        await runtime.execute(
            "UPDATE external_orders SET product_name='legacy-updated' WHERE tenant_id=$1 AND id=$2",
            tenant_id,
            legacy_crud_id,
        )
        assert await runtime.fetchval(
            "SELECT product_name='legacy-updated' FROM external_orders WHERE tenant_id=$1 AND id=$2",
            tenant_id,
            legacy_crud_id,
        )
        await runtime.execute("DELETE FROM external_orders WHERE tenant_id=$1 AND id=$2", tenant_id, legacy_crud_id)
        assert not await runtime.fetchval(
            "SELECT EXISTS(SELECT 1 FROM external_orders WHERE tenant_id=$1 AND id=$2)", tenant_id, legacy_crud_id
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchval("SELECT count(*) FROM external_order_value_receipts")
    finally:
        await runtime.close()
    await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)


@pytest.mark.parametrize("fact_kind", ["event", "receipt", "runtime"])
async def test_current_head_preflights_each_u8a_runtime_fact_without_drift(
    migrated_pg_url: str,
    fact_kind: str,
) -> None:
    current_head = _sole_current_head_with_u8a_ancestor()
    database_name = f"yimatong_acceptance_u8a_{fact_kind}_{uuid.uuid4().hex[:8]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    owner: asyncpg.Connection | None = None
    runtime: asyncpg.Connection | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(_owner_dsn(database_url))
        tenant_id, actor_id = await _seed_actor(owner, f"u8a-head-{fact_kind}")
        auth_session_id = await owner.fetchval(
            "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2",
            tenant_id,
            actor_id,
        )

        if fact_kind == "runtime":
            await owner.execute(f'GRANT CONNECT ON DATABASE "{database_name}" TO yimatong_app')
            await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
            runtime = await asyncpg.connect(_runtime_dsn(database_url))
            await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
            await _call(
                runtime,
                tenant_id,
                auth_session_id,
                "order_confirmed",
                Decimal("12.500000"),
                "u8a-runtime-fact",
                "runtime-fact",
            )
        else:
            order_id, receipt_id, event_id = uuid7(), uuid7(), uuid7()
            await owner.execute(
                "INSERT INTO external_orders(id,tenant_id,external_id,amount,matched,source_system,status,refund_amount,"
                "currency,ledger_original_amount,ledger_refunded_amount,ledger_cancelled_amount,ledger_net_amount,"
                "ledger_status,created_at,updated_at) VALUES($1,$2,$3,12.5,false,'erp','paid',0,'CNY',"
                "12.5,0,0,12.5,'paid',now(),now())",
                order_id,
                tenant_id,
                f"{fact_kind.upper()}-FACT",
            )
            async with owner.transaction():
                await owner.execute(
                    "INSERT INTO external_order_value_receipts(id,tenant_id,source_system,external_order_id,event_type,"
                    "idempotency_key,payload_digest,order_id,event_id,result_original_amount,result_refunded_amount,"
                    "result_cancelled_amount,result_net_amount,result_status) VALUES($1,$2,'erp',$3,'order_confirmed',"
                    "$4,$5,$6,$7,12.5,0,0,12.5,'paid')",
                    receipt_id,
                    tenant_id,
                    f"{fact_kind.upper()}-FACT",
                    "backfill:confirmed:" + str(order_id) if fact_kind == "event" else "receipt-owned-fact",
                    "a" * 64,
                    order_id,
                    event_id,
                )
                await owner.execute(
                    "INSERT INTO external_order_value_events(id,tenant_id,order_id,receipt_id,sequence_no,event_type,"
                    "event_amount,currency,source_system,external_order_id,provenance_type,provenance_digest,"
                    "provenance_verified,actor_type,actor_id,reason,occurred_at) VALUES($1,$2,$3,$4,1,"
                    "'order_confirmed',12.5,'CNY','erp',$5,$6,$7,false,$8,$9,'acceptance fact',now())",
                    event_id,
                    tenant_id,
                    order_id,
                    receipt_id,
                    f"{fact_kind.upper()}-FACT",
                    "manual_import" if fact_kind == "event" else "backfill",
                    "a" * 64,
                    "account" if fact_kind == "event" else "migration",
                    actor_id if fact_kind == "event" else None,
                )

        before = await _u8a_head_snapshot(owner, tenant_id)
        blocked = await asyncio.to_thread(_alembic, database_url, "downgrade", "u8a2e3f4a5b6", succeeds=False)
        assert "immutable external order ledger facts" in (blocked.stdout + blocked.stderr).lower()
        assert await _u8a_head_snapshot(owner, tenant_id) == before
        assert before[0] == current_head
    finally:
        if runtime is not None:
            await runtime.close()
        if owner is not None:
            await owner.close()
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=lease.created and not lease.marker_written,
            )


async def test_current_head_clean_u8a_target_downgrade_and_restore(migrated_pg_url: str) -> None:
    database_name = f"yimatong_acceptance_u8a_clean_{uuid.uuid4().hex[:10]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    owner: asyncpg.Connection | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u8a2e3f4a5b6")
        owner = await asyncpg.connect(_owner_dsn(database_url))
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8a2e3f4a5b6"
        await owner.close()
        owner = None
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
    finally:
        if owner is not None:
            await owner.close()
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=lease.created and not lease.marker_written,
            )


async def test_a_empty_ledger_downgrades_to_parent_and_reupgrades(migrated_pg_url: str) -> None:
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u6l4a5b6c7d8")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT to_regclass('public.external_order_value_events') IS NULL")
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='external_orders' AND column_name='ledger_net_amount')"
        )
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_db_authority_derives_actor_from_live_manage_session(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    tenant_id, actor_id = await _seed_actor(owner, "u8a-session-authority")
    auth_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", tenant_id, actor_id
    )
    admin_role_id = await owner.fetchval(
        "SELECT ar.role_id FROM account_roles ar JOIN roles role "
        "ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id "
        "WHERE ar.tenant_id=$1 AND ar.account_id=$2 AND role.name='admin'",
        tenant_id,
        actor_id,
    )
    backup_admin_id = uuid7()
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,is_active,"
        "auth_version,created_at,updated_at) SELECT $1,tenant_id,organization_id,$2,'x','backup admin',0,true,1,"
        "now(),now() FROM accounts WHERE id=$3",
        backup_admin_id,
        f"u8a-backup-admin-{backup_admin_id.hex[:8]}@example.test",
        actor_id,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        tenant_id,
        backup_admin_id,
        admin_role_id,
    )
    foreign_tenant_id, foreign_actor_id = await _seed_actor(owner, "u8a-foreign-session")
    foreign_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", foreign_tenant_id, foreign_actor_id
    )
    viewer_role_id, viewer_id, viewer_session_id = uuid7(), uuid7(), uuid7()
    await owner.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'viewer',now(),now())",
        viewer_role_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,is_active,"
        "auth_version,created_at,updated_at) SELECT $1,tenant_id,organization_id,$2,'x','viewer',0,true,1,now(),now() "
        "FROM accounts WHERE id=$3",
        viewer_id,
        f"u8a-viewer-{viewer_id.hex[:8]}@example.test",
        actor_id,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        tenant_id,
        viewer_id,
        viewer_role_id,
    )
    await owner.execute(
        "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,1,$4,now()+interval '1 hour',now(),now())",
        viewer_session_id,
        tenant_id,
        viewer_id,
        uuid.uuid4().hex,
    )

    async def assert_denied(session_id: uuid.UUID, sqlstate: str, label: str) -> None:
        before = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM external_orders WHERE tenant_id=$1) orders,"
            "(SELECT count(*) FROM external_order_value_receipts WHERE tenant_id=$1) receipts,"
            "(SELECT count(*) FROM external_order_value_events WHERE tenant_id=$1) events",
            tenant_id,
        )
        with pytest.raises(asyncpg.PostgresError) as raised:
            await _call(runtime, tenant_id, session_id, "order_confirmed", Decimal("1"), label, label)
        assert raised.value.sqlstate == sqlstate
        after = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM external_orders WHERE tenant_id=$1) orders,"
            "(SELECT count(*) FROM external_order_value_receipts WHERE tenant_id=$1) receipts,"
            "(SELECT count(*) FROM external_order_value_events WHERE tenant_id=$1) events",
            tenant_id,
        )
        assert after == before

    try:
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        await assert_denied(uuid7(), "23503", "missing-session")
        await assert_denied(foreign_session_id, "23503", "foreign-session")
        await assert_denied(viewer_session_id, "42501", "viewer-session")

        await owner.execute(
            "UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE id=$1", auth_session_id
        )
        await assert_denied(auth_session_id, "42501", "expired-session")
        await owner.execute("UPDATE auth_sessions SET expires_at=now()+interval '1 hour' WHERE id=$1", auth_session_id)

        await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", auth_session_id)
        await assert_denied(auth_session_id, "42501", "revoked-session")
        await owner.execute("UPDATE auth_sessions SET revoked_at=NULL WHERE id=$1", auth_session_id)

        await owner.execute("UPDATE auth_sessions SET current_refresh_jti='' WHERE id=$1", auth_session_id)
        await assert_denied(auth_session_id, "42501", "empty-refresh-session")
        await owner.execute(
            "UPDATE auth_sessions SET current_refresh_jti=$2 WHERE id=$1", auth_session_id, uuid.uuid4().hex
        )

        await owner.execute("UPDATE auth_sessions SET auth_version=0 WHERE id=$1", auth_session_id)
        await assert_denied(auth_session_id, "42501", "stale-session")
        await owner.execute("UPDATE auth_sessions SET auth_version=1 WHERE id=$1", auth_session_id)

        await owner.execute("UPDATE accounts SET is_active=false WHERE id=$1", actor_id)
        await assert_denied(auth_session_id, "42501", "inactive-account")
        await owner.execute("UPDATE accounts SET is_active=true WHERE id=$1", actor_id)

        role_permission = await owner.fetchrow(
            "SELECT ar.role_id,rp.permission_id FROM account_roles ar JOIN role_permissions rp "
            "ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id JOIN permissions p "
            "ON p.tenant_id=rp.tenant_id AND p.id=rp.permission_id "
            "WHERE ar.tenant_id=$1 AND ar.account_id=$2 AND p.code='order:manage'",
            tenant_id,
            actor_id,
        )
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            tenant_id,
            role_permission["role_id"],
            role_permission["permission_id"],
        )
        await assert_denied(auth_session_id, "42501", "missing-manage-grant")
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            tenant_id,
            role_permission["role_id"],
            role_permission["permission_id"],
        )

        confirmed = await _call(
            runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("1"), "valid-session", "valid-session"
        )
        event = await owner.fetchrow(
            "SELECT actor_type,actor_id,provenance_type,provenance_verified FROM external_order_value_events "
            "WHERE id=$1",
            confirmed["event_id"],
        )
        assert dict(event) == {
            "actor_type": "account",
            "actor_id": actor_id,
            "provenance_type": "manual_import",
            "provenance_verified": False,
        }
    finally:
        await runtime.close()
        await owner.close()


async def test_value_ledger_replay_refund_cancel_immutability_and_acl(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    tenant_id, actor_id = await _seed_actor(owner, "u8a-ledger")
    auth_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", tenant_id, actor_id
    )
    try:
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        confirmed = await _call(
            runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("100.10"), "order-1", "confirmed"
        )
        replay = await _call(
            runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("100.10"), "order-1", "confirmed"
        )
        assert replay["receipt_id"] == confirmed["receipt_id"]
        assert replay["event_id"] == confirmed["event_id"]
        assert replay["replayed"] is True
        with pytest.raises(asyncpg.UniqueViolationError):
            await _call(runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("100.11"), "order-1", "changed")

        first = await _call(runtime, tenant_id, auth_session_id, "refund", Decimal("30.05"), "refund-1", "refund one")
        assert first["net_amount"] == Decimal("70.050000")
        with pytest.raises(asyncpg.InvalidParameterValueError):
            await _call(runtime, tenant_id, auth_session_id, "refund", Decimal("70.06"), "refund-too-high", "too high")
        cancelled = await _call(runtime, tenant_id, auth_session_id, "cancel", None, "cancel-1", "cancel")
        assert cancelled["event_amount"] == Decimal("70.050000")
        assert cancelled["net_amount"] == 0
        assert cancelled["status"] == "cancelled"
        with pytest.raises(asyncpg.InvalidParameterValueError):
            await _call(runtime, tenant_id, auth_session_id, "refund", Decimal("1"), "refund-after-cancel", "terminal")

        assert await owner.fetchval(
            "SELECT ledger_refunded_amount+ledger_cancelled_amount+ledger_net_amount=ledger_original_amount "
            "FROM external_orders WHERE tenant_id=$1 AND id=$2",
            tenant_id,
            confirmed["order_id"],
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute("DELETE FROM external_orders WHERE id=$1", confirmed["order_id"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "UPDATE external_order_value_events SET reason='tampered' WHERE id=$1", confirmed["event_id"]
            )
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="immutable"):
            await owner.execute("DELETE FROM external_order_value_events WHERE id=$1", confirmed["event_id"])
    finally:
        await runtime.close()
        await owner.close()


async def test_value_ledger_contention_is_bounded_zero_write_then_retry(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    locker = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    tenant_id, actor_id = await _seed_actor(owner, "u8a-contention")
    auth_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", tenant_id, actor_id
    )
    transaction = locker.transaction()
    try:
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        await transaction.start()
        assert await locker.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtextextended('external-order:'||$1::uuid||':erp:ORDER-1',0))",
            tenant_id,
        )
        with pytest.raises(asyncpg.LockNotAvailableError):
            await asyncio.wait_for(
                _call(runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("42.00"), "busy-order", "busy"),
                timeout=1,
            )
        assert (
            await owner.fetchval("SELECT count(*) FROM external_order_value_receipts WHERE tenant_id=$1", tenant_id)
            == 0
        )
        assert await owner.fetchval("SELECT count(*) FROM external_orders WHERE tenant_id=$1", tenant_id) == 0
        await transaction.commit()
        retried = await _call(
            runtime, tenant_id, auth_session_id, "order_confirmed", Decimal("42.00"), "busy-order", "busy"
        )
        assert retried["net_amount"] == Decimal("42.000000")
    finally:
        if locker.is_in_transaction():
            await transaction.rollback()
        await runtime.close()
        await locker.close()
        await owner.close()


async def test_z_downgrade_refuses_post_cutover_immutable_facts(migrated_pg_url: str) -> None:
    current_head = _sole_current_head_with_u8a_ancestor()
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        tenant_id = await owner.fetchval(
            "SELECT tenant_id FROM external_order_value_events WHERE provenance_type<>'backfill' ORDER BY recorded_at LIMIT 1"
        )
        assert tenant_id is not None
        before = await _u8a_head_snapshot(owner, tenant_id)
        blocked = await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u8a1d2e3f4a5", succeeds=False)
        assert "immutable external order ledger facts" in (blocked.stdout + blocked.stderr).lower()
        assert await _u8a_head_snapshot(owner, tenant_id) == before
        assert before[0] == current_head
    finally:
        await owner.close()
