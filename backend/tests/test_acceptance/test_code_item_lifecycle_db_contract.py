"""Real-PostgreSQL contract for function-only code-item lifecycle writes."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR
from tests.test_acceptance.test_code_batch_delivery_contract import (
    _insert_batch,
    _insert_items,
    _insert_manifest_and_deliver,
    _insert_receipt,
    _purge_owned_delivery_fixture,
    _seed_catalog,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "4a92d1e3f5b7"
INDEX_REVISION = "8d42f6b0c3e5"
ATTACH_REVISION = "9e53a7c1d4f6"
BACKFILL_REVISION = "af64b8d2e5a7"
CHECK_REVISION = "b075c9e3f6b8"
VALIDATE_REVISION = "c186daf407c9"
HEAD_REVISION = "d297eb0518da"
BACKFILL_INDEX = "ix_code_items_frozen_provenance_backfill"
DOWNGRADE_IDENTITY_INDEX = "uq_code_items_tenant_id_id_downgrade"


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "database_url": database_url,
            "migration_database_url": database_url,
            "control_database_url": database_url,
        }
    )
    command = [sys.executable, "-m", "alembic"]
    if args == ("check",):
        command.extend(["-x", "baseline_legacy_timestamp_nullability=true"])
    command.extend(args)
    result = subprocess.run(command, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=180)
    assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
    return result


async def _runtime_call(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    sql: str,
    *args: object,
) -> asyncpg.Record:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        return await conn.fetchrow(sql, *args)


async def _assert_sqlstate(coro, sqlstate: str) -> None:
    with pytest.raises(asyncpg.PostgresError) as raised:
        await coro
    assert raised.value.sqlstate == sqlstate


async def _leave_invalid_concurrent_index(
    locker: asyncpg.Connection,
    observer: asyncpg.Connection,
    owner_dsn: str,
    item_id: uuid.UUID,
    index_name: str,
    create_sql: str,
) -> None:
    """Cancel a real concurrent build after PostgreSQL publishes its invalid shell."""

    assert await observer.fetchval("SELECT to_regclass('public.' || $1) IS NULL", index_name)
    blocking_tx = locker.transaction()
    builder = await asyncpg.connect(owner_dsn)
    build_task: asyncio.Task[str] | None = None
    await blocking_tx.start()
    try:
        await locker.execute("UPDATE code_items SET updated_at=updated_at WHERE id=$1", item_id)
        builder_pid = await builder.fetchval("SELECT pg_backend_pid()")
        build_task = asyncio.create_task(builder.execute(create_sql))
        invalid_published = False
        for _ in range(100):
            invalid_published = (
                await observer.fetchval(
                    "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
                    index_name,
                )
                is False
            )
            if invalid_published:
                break
            await asyncio.sleep(0.05)
        assert invalid_published
        assert await observer.fetchval("SELECT pg_cancel_backend($1)", builder_pid)
        with pytest.raises(asyncpg.QueryCanceledError):
            await asyncio.wait_for(build_task, timeout=5)
        build_task = None
    finally:
        if build_task is not None and not build_task.done():
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)
        await builder.close()
        await blocking_tx.rollback()
    assert (
        await observer.fetchval(
            "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
            index_name,
        )
        is False
    )


async def test_staged_backfill_keeps_unrelated_runtime_io_available_and_cutover_bounded(
    migrated_pg_url: str,
) -> None:
    """Prove real commit boundaries, online backfill, and atomic final cutover."""

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    observer = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    backfill_task: asyncio.Task[subprocess.CompletedProcess[str]] | None = None
    locked_backfill_row = owner.transaction()
    cutover_writer = runtime.transaction()
    index_repair_blocker = owner.transaction()
    check_drop_blocker = owner.transaction()
    attach_drop_blocker = owner.transaction()
    backfill_lock_active = False
    cutover_writer_active = False
    index_repair_blocker_active = False
    check_drop_blocker_active = False
    attach_drop_blocker_active = False
    try:
        await owner.execute(
            "CREATE TABLE acceptance_u03b_migration_txids(revision text NOT NULL, transaction_id bigint NOT NULL)"
        )
        await owner.execute(
            """
            CREATE FUNCTION log_acceptance_u03b_migration_txid()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                INSERT INTO acceptance_u03b_migration_txids(revision,transaction_id)
                VALUES(NEW.version_num,txid_current());
                RETURN NEW;
            END
            $function$
            """
        )
        await owner.execute(
            "CREATE TRIGGER trg_log_acceptance_u03b_migration_txid "
            "AFTER INSERT OR UPDATE OF version_num ON alembic_version FOR EACH ROW "
            "EXECUTE FUNCTION log_acceptance_u03b_migration_txid()"
        )

        ids = await _seed_catalog(owner, "staged-online")
        receipt_id = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt_id, quantity=3, expected_item_count=3)
        await _insert_items(owner, ids["tenant"], batch_id, 3)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
        await _insert_manifest_and_deliver(owner, ids, batch_id, row_count=3)
        async with owner.transaction():
            await owner.execute(
                "UPDATE code_items SET status='activated',activated_at=now() WHERE code_batch_id=$1",
                batch_id,
            )
            await owner.execute("UPDATE code_batches SET status='activated' WHERE id=$1", batch_id)
        items = await owner.fetch(
            "SELECT id,public_id FROM code_items WHERE code_batch_id=$1 ORDER BY id::text",
            batch_id,
        )
        await owner.execute("UPDATE code_items SET status='frozen' WHERE id=$1", items[0]["id"])

        _alembic(migrated_pg_url, "upgrade", ATTACH_REVISION)
        revision_txids = await owner.fetch(
            "SELECT revision,transaction_id FROM acceptance_u03b_migration_txids "
            "WHERE revision IN ('7c31e5a9b2d4','8d42f6b0c3e5','9e53a7c1d4f6') ORDER BY revision"
        )
        assert {row["revision"] for row in revision_txids} == {
            "7c31e5a9b2d4",
            "8d42f6b0c3e5",
            ATTACH_REVISION,
        }
        assert len({row["transaction_id"] for row in revision_txids}) == 3
        assert not await owner.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname='fk_interception_records_tenant_code_item'"
        )

        # The expand compatibility trigger keeps exact old-runtime lifecycle
        # writes valid until the final interface/ACL cutover.
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await runtime.execute("UPDATE code_items SET status='frozen' WHERE id=$1", items[1]["id"])
        assert (
            await owner.fetchval(
                "SELECT freeze_provenance_version FROM code_items WHERE id=$1",
                items[1]["id"],
            )
            == 0
        )

        await locked_backfill_row.start()
        backfill_lock_active = True
        await owner.execute("SELECT id FROM code_items WHERE id=$1 FOR UPDATE", items[0]["id"])
        backfill_task = asyncio.create_task(asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", BACKFILL_REVISION))
        waiting = False
        for _ in range(60):
            waiting = bool(
                await observer.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                    "AND query LIKE '%freeze_provenance_version IS NULL%')"
                )
            )
            if waiting:
                break
            await asyncio.sleep(0.05)
        assert waiting
        # Earlier revisions from the same Alembic invocation are durably
        # committed while the backfill revision is still waiting.
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == ATTACH_REVISION

        started_at = time.perf_counter()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            assert (
                await runtime.fetchval(
                    "SELECT public_id FROM code_items WHERE id=$1",
                    items[2]["id"],
                )
                == items[2]["public_id"]
            )
            await runtime.execute(
                "UPDATE code_items SET first_scanned_at=COALESCE(first_scanned_at,now()) WHERE id=$1",
                items[2]["id"],
            )
        assert time.perf_counter() - started_at < 1.0

        await locked_backfill_row.rollback()
        backfill_lock_active = False
        await asyncio.wait_for(backfill_task, timeout=15)
        backfill_task = None
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == BACKFILL_REVISION
        assert (
            await observer.fetchval(
                "SELECT freeze_provenance_version FROM code_items WHERE id=$1",
                items[0]["id"],
            )
            == 0
        )

        _alembic(migrated_pg_url, "upgrade", VALIDATE_REVISION)
        assert await observer.fetchval(
            "SELECT bool_and(convalidated) FROM pg_constraint "
            "WHERE conname IN ('ck_code_items_frozen_provenance','fk_interception_records_tenant_code_item')"
        )

        await cutover_writer.start()
        cutover_writer_active = True
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        await runtime.execute(
            "UPDATE code_items SET first_scanned_at=first_scanned_at WHERE id=$1",
            items[2]["id"],
        )
        cutover_started = time.perf_counter()
        blocked_cutover = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "upgrade",
            "head",
            succeeds=False,
        )
        cutover_elapsed = time.perf_counter() - cutover_started
        assert 4.0 <= cutover_elapsed < 8.0
        assert "lock timeout" in f"{blocked_cutover.stdout}\n{blocked_cutover.stderr}".lower()
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == VALIDATE_REVISION
        assert await observer.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        assert not await observer.fetchval(
            "SELECT to_regprocedure('public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)') IS NOT NULL"
        )
        assert await observer.fetchval(
            "SELECT to_regprocedure('public.populate_legacy_frozen_provenance()') IS NOT NULL"
        )
        await cutover_writer.rollback()
        cutover_writer_active = False

        _alembic(migrated_pg_url, "upgrade", "head")
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert not await observer.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        assert await observer.fetchval(
            "SELECT to_regprocedure('public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)') IS NOT NULL"
        )
        assert not await observer.fetchval(
            "SELECT to_regprocedure('public.populate_legacy_frozen_provenance()') IS NOT NULL"
        )

        # Cycle A: downgrade of b075 repairs a real invalid concurrent index in
        # autocommit before dropping the CHECK in its separate short transaction.
        _alembic(migrated_pg_url, "downgrade", CHECK_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == CHECK_REVISION
        await _leave_invalid_concurrent_index(
            owner,
            observer,
            owner_dsn,
            items[2]["id"],
            BACKFILL_INDEX,
            "CREATE INDEX CONCURRENTLY ix_code_items_frozen_provenance_backfill "
            "ON public.code_items (id) WHERE status='frozen' AND freeze_provenance_version IS NULL",
        )
        check_catalog_before = await observer.fetchrow(
            "SELECT pg_get_constraintdef(oid) definition,convalidated "
            "FROM pg_constraint WHERE conrelid='public.code_items'::regclass "
            "AND conname='ck_code_items_frozen_provenance'"
        )
        await index_repair_blocker.start()
        index_repair_blocker_active = True
        await owner.execute("LOCK TABLE public.code_items IN SHARE UPDATE EXCLUSIVE MODE")
        repair_started = time.perf_counter()
        blocked_repair = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            BACKFILL_REVISION,
            succeeds=False,
        )
        assert 4.0 <= time.perf_counter() - repair_started < 8.0
        assert "lock timeout" in f"{blocked_repair.stdout}\n{blocked_repair.stderr}".lower()
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == CHECK_REVISION
        assert (
            await observer.fetchrow(
                "SELECT pg_get_constraintdef(oid) definition,convalidated "
                "FROM pg_constraint WHERE conrelid='public.code_items'::regclass "
                "AND conname='ck_code_items_frozen_provenance'"
            )
            == check_catalog_before
        )
        assert (
            await observer.fetchval(
                "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
                BACKFILL_INDEX,
            )
            is False
        )
        await index_repair_blocker.rollback()
        index_repair_blocker_active = False
        _alembic(migrated_pg_url, "downgrade", BACKFILL_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == BACKFILL_REVISION
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint "
            "WHERE conrelid='public.code_items'::regclass AND conname='ck_code_items_frozen_provenance')"
        )
        assert await observer.fetchval(
            "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
            BACKFILL_INDEX,
        )
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index idx "
            "WHERE NOT idx.indisvalid AND idx.indexrelid IN "
            "(to_regclass('public.' || $1),to_regclass('public.' || $2)))",
            BACKFILL_INDEX,
            DOWNGRADE_IDENTITY_INDEX,
        )

        # Cycle B: return to b075 and prebuild a valid replacement. Holding only
        # the CHECK's table lock now injects failure after autocommit preparation.
        _alembic(migrated_pg_url, "upgrade", CHECK_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == CHECK_REVISION
        assert await observer.fetchval("SELECT to_regclass('public.' || $1) IS NULL", BACKFILL_INDEX)
        await observer.execute(
            "CREATE INDEX CONCURRENTLY ix_code_items_frozen_provenance_backfill "
            "ON public.code_items (id) WHERE status='frozen' AND freeze_provenance_version IS NULL"
        )
        await check_drop_blocker.start()
        check_drop_blocker_active = True
        await owner.execute("LOCK TABLE public.code_items IN ROW SHARE MODE")
        check_drop_started = time.perf_counter()
        blocked_check_drop = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            BACKFILL_REVISION,
            succeeds=False,
        )
        assert 4.0 <= time.perf_counter() - check_drop_started < 8.0
        assert "lock timeout" in f"{blocked_check_drop.stdout}\n{blocked_check_drop.stderr}".lower()
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == CHECK_REVISION
        assert await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint "
            "WHERE conrelid='public.code_items'::regclass AND conname='ck_code_items_frozen_provenance')"
        )
        assert await observer.fetchval(
            "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
            BACKFILL_INDEX,
        )
        await check_drop_blocker.rollback()
        check_drop_blocker_active = False
        _alembic(migrated_pg_url, "downgrade", BACKFILL_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == BACKFILL_REVISION
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint "
            "WHERE conrelid='public.code_items'::regclass AND conname='ck_code_items_frozen_provenance')"
        )
        assert await observer.fetchval(
            "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
            BACKFILL_INDEX,
        )

        # Cycle A: 9e53 repairs an invalid temporary unique index, then swaps it
        # to the canonical 8d42 identity-index name in the successful short tx.
        _alembic(migrated_pg_url, "downgrade", ATTACH_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == ATTACH_REVISION
        await _leave_invalid_concurrent_index(
            owner,
            observer,
            owner_dsn,
            items[2]["id"],
            DOWNGRADE_IDENTITY_INDEX,
            "CREATE UNIQUE INDEX CONCURRENTLY uq_code_items_tenant_id_id_downgrade ON public.code_items (tenant_id,id)",
        )
        _alembic(migrated_pg_url, "downgrade", INDEX_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == INDEX_REVISION
        assert await observer.fetchval(
            "SELECT idx.indisvalid AND idx.indisunique FROM pg_index idx "
            "WHERE idx.indexrelid=to_regclass('public.uq_code_items_tenant_id_id')"
        )
        assert not await observer.fetchval(
            "SELECT to_regclass('public.' || $1) IS NOT NULL",
            DOWNGRADE_IDENTITY_INDEX,
        )

        # Cycle B: restore 9e53, prebuild its valid temporary index, then block
        # the later FK drop. Earlier trigger/function drops must roll back with
        # the FK/UQ changes while the prepared index remains reusable.
        _alembic(migrated_pg_url, "upgrade", ATTACH_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == ATTACH_REVISION
        await observer.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY uq_code_items_tenant_id_id_downgrade ON public.code_items (tenant_id,id)"
        )
        await attach_drop_blocker.start()
        attach_drop_blocker_active = True
        await owner.execute("LOCK TABLE public.interception_records IN ROW SHARE MODE")
        attach_drop_started = time.perf_counter()
        blocked_attach_drop = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            INDEX_REVISION,
            succeeds=False,
        )
        assert 4.0 <= time.perf_counter() - attach_drop_started < 8.0
        assert "lock timeout" in f"{blocked_attach_drop.stdout}\n{blocked_attach_drop.stderr}".lower()
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == ATTACH_REVISION
        assert await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_trigger "
            "WHERE tgrelid='public.code_items'::regclass "
            "AND tgname='trg_populate_legacy_frozen_provenance' AND NOT tgisinternal)"
        )
        assert await observer.fetchval(
            "SELECT to_regprocedure('public.populate_legacy_frozen_provenance()') IS NOT NULL"
        )
        assert await observer.fetchval(
            "SELECT count(*)=2 FROM pg_constraint WHERE conname IN "
            "('fk_interception_records_tenant_code_item','uq_code_items_tenant_id_id')"
        )
        assert await observer.fetchval(
            "SELECT conindid=to_regclass('public.uq_code_items_tenant_id_id') "
            "FROM pg_constraint WHERE conname='uq_code_items_tenant_id_id'"
        )
        assert await observer.fetchval(
            "SELECT idx.indisvalid FROM pg_index idx WHERE idx.indexrelid=to_regclass('public.' || $1)",
            DOWNGRADE_IDENTITY_INDEX,
        )
        await attach_drop_blocker.rollback()
        attach_drop_blocker_active = False
        _alembic(migrated_pg_url, "downgrade", INDEX_REVISION)
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == INDEX_REVISION
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname IN "
            "('fk_interception_records_tenant_code_item','uq_code_items_tenant_id_id'))"
        )
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_trigger "
            "WHERE tgrelid='public.code_items'::regclass "
            "AND tgname='trg_populate_legacy_frozen_provenance' AND NOT tgisinternal)"
        )
        assert not await observer.fetchval(
            "SELECT to_regprocedure('public.populate_legacy_frozen_provenance()') IS NOT NULL"
        )
        assert await observer.fetchval(
            "SELECT idx.indisvalid AND idx.indisunique FROM pg_index idx "
            "WHERE idx.indexrelid=to_regclass('public.uq_code_items_tenant_id_id')"
        )
        assert not await observer.fetchval(
            "SELECT to_regclass('public.' || $1) IS NOT NULL",
            DOWNGRADE_IDENTITY_INDEX,
        )

        await _purge_owned_delivery_fixture(owner, ids["tenant"])
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='code_items' AND column_name='frozen_from_status')"
        )
        assert await observer.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        await owner.execute("DROP TRIGGER trg_log_acceptance_u03b_migration_txid ON alembic_version")
        await owner.execute("DROP FUNCTION log_acceptance_u03b_migration_txid()")
        await owner.execute("DROP TABLE acceptance_u03b_migration_txids")
        _alembic(migrated_pg_url, "upgrade", "head")
    finally:
        if backfill_task is not None:
            if backfill_lock_active:
                await locked_backfill_row.rollback()
            await asyncio.wait_for(backfill_task, timeout=15)
        if cutover_writer_active:
            await cutover_writer.rollback()
        if index_repair_blocker_active:
            await index_repair_blocker.rollback()
        if check_drop_blocker_active:
            await check_drop_blocker.rollback()
        if attach_drop_blocker_active:
            await attach_drop_blocker.rollback()
        await runtime.close()
        await observer.close()
        await owner.close()


async def test_function_only_lifecycle_risk_replay_first_scan_and_roundtrip(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "lifecycle")
    receipt_id = await _insert_receipt(owner, ids)
    batch_id = await _insert_batch(owner, ids, receipt_id)
    await _insert_items(owner, ids["tenant"], batch_id, 2)
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
    await _insert_manifest_and_deliver(owner, ids, batch_id, row_count=2)
    auth_session_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        auth_session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )
    items = await owner.fetch(
        "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id::text",
        ids["tenant"],
        batch_id,
    )
    owned_audits: list[uuid.UUID] = []
    owned_alerts: list[uuid.UUID] = []
    owned_interceptions: list[uuid.UUID] = []
    owned_rules: list[uuid.UUID] = []
    try:
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        for signature in (
            "record_public_code_scan(uuid,text,uuid,text,text,text,text)",
            "transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)",
            "transition_code_batch_lifecycle(uuid,uuid,uuid,uuid,text,text)",
            "freeze_code_item_for_risk(uuid,uuid,uuid,uuid,uuid)",
        ):
            assert await owner.fetchval(
                "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
                signature,
            )
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            "mark_code_item_first_scanned(uuid,text)",
        )

        invalid_provenance = (
            (None, True, "actor", "reason", 1),
            ("activated", False, "actor", "reason", 1),
            ("activated", True, "actor", "reason", None),
            ("activated", True, None, "reason", 1),
            ("activated", True, "actor", None, 1),
            ("activated", True, "actor", None, 0),
            ("activated", True, None, "reason", 0),
        )
        async with owner.transaction():
            await owner.execute("ALTER TABLE public.code_items DISABLE TRIGGER USER")
            try:
                for frozen_from, has_frozen_at, frozen_by, freeze_reason, version in invalid_provenance:
                    with pytest.raises(asyncpg.CheckViolationError):
                        async with owner.transaction():
                            await owner.execute(
                                "UPDATE code_items SET status='frozen',frozen_from_status=$2,"
                                "frozen_at=CASE WHEN $3 THEN now() END,frozen_by=$4,freeze_reason=$5,"
                                "freeze_provenance_version=$6 WHERE id=$1",
                                items[0]["id"],
                                frozen_from,
                                has_frozen_at,
                                frozen_by,
                                freeze_reason,
                                version,
                            )
            finally:
                await owner.execute("ALTER TABLE public.code_items ENABLE TRIGGER USER")

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await _assert_sqlstate(
                runtime.execute(
                    "UPDATE code_items SET status='frozen',frozen_from_status=NULL,frozen_at=now(),"
                    "frozen_by='actor',freeze_reason='reason',freeze_provenance_version=1 WHERE id=$1",
                    items[0]["id"],
                ),
                "42501",
            )

        await owner.execute(
            "UPDATE production_batches SET production_date="
            "(now() AT TIME ZONE 'Asia/Shanghai')::date-2, expiry_date="
            "(now() AT TIME ZONE 'Asia/Shanghai')::date-1 WHERE id=$1",
            ids["production_batch"],
        )
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                ids["tenant"],
                auth_session_id,
                uuid.uuid4(),
                batch_id,
            ),
            "22023",
        )
        await owner.execute(
            "UPDATE production_batches SET production_date="
            "(now() AT TIME ZONE 'Asia/Shanghai')::date, expiry_date="
            "(now() AT TIME ZONE 'Asia/Shanghai')::date+365 WHERE id=$1",
            ids["production_batch"],
        )

        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        activated = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
            ids["tenant"],
            auth_session_id,
            audit_id,
            batch_id,
        )
        assert activated["affected_item_count"] == 2
        assert activated["current_status"] == "activated"

        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        bound = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'bind',NULL)",
            ids["tenant"],
            auth_session_id,
            audit_id,
            items[0]["id"],
        )
        assert bound["prior_status"] == "activated" and bound["current_status"] == "bound"
        bound_at = await owner.fetchval("SELECT bound_at FROM code_items WHERE id=$1", items[0]["id"])

        for action in ("freeze", "recover"):
            audit_id = uuid.uuid4()
            owned_audits.append(audit_id)
            row = await _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,$5,$6)",
                ids["tenant"],
                auth_session_id,
                audit_id,
                items[0]["id"],
                action,
                "manual investigation" if action == "freeze" else None,
            )
        assert row["current_status"] == "bound"
        assert await owner.fetchval("SELECT bound_at FROM code_items WHERE id=$1", items[0]["id"]) == bound_at

        rule_id = uuid.uuid4()
        interception_id = uuid.uuid4()
        null_interception_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        risk_audit_id = uuid.uuid4()
        owned_rules.append(rule_id)
        owned_interceptions.append(interception_id)
        owned_interceptions.append(null_interception_id)
        owned_alerts.append(alert_id)
        owned_audits.append(risk_audit_id)
        await owner.execute(
            "INSERT INTO risk_rules(id,tenant_id,name,rule_type,action,config,enabled,created_at,updated_at) "
            "VALUES($1,$2,'auto block','suspected_copy','block','{\"version\":\"v9\"}'::json,true,now(),now())",
            rule_id,
            ids["tenant"],
        )
        await owner.execute(
            "INSERT INTO interception_records "
            "(id,tenant_id,risk_rule_id,action,context,auto_triggered,created_at,updated_at) "
            "VALUES($1,$2,$3,'block','{}'::json,true,now(),now())",
            null_interception_id,
            ids["tenant"],
            rule_id,
        )
        null_alert_id = uuid.uuid4()
        null_audit_id = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM freeze_code_item_for_risk($1,$2,$3,$4,$5)",
                ids["tenant"],
                null_interception_id,
                items[0]["id"],
                null_alert_id,
                null_audit_id,
            ),
            "22023",
        )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM risk_alerts WHERE id=$1)", null_alert_id)
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", null_audit_id)
        await owner.execute(
            "INSERT INTO interception_records "
            "(id,tenant_id,risk_rule_id,code_item_id,action,context,auto_triggered,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'block','{}'::json,true,now(),now())",
            interception_id,
            ids["tenant"],
            rule_id,
            items[0]["id"],
        )
        mismatch_alert_id = uuid.uuid4()
        mismatch_audit_id = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM freeze_code_item_for_risk($1,$2,$3,$4,$5)",
                ids["tenant"],
                interception_id,
                items[1]["id"],
                mismatch_alert_id,
                mismatch_audit_id,
            ),
            "22023",
        )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM risk_alerts WHERE id=$1)", mismatch_alert_id)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", mismatch_audit_id
        )
        frozen = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM freeze_code_item_for_risk($1,$2,$3,$4,$5)",
            ids["tenant"],
            interception_id,
            items[0]["id"],
            alert_id,
            risk_audit_id,
        )
        assert frozen["prior_status"] == "bound" and frozen["current_status"] == "frozen"
        provenance = await owner.fetchrow(
            "SELECT frozen_from_status,frozen_by,freeze_reason,freeze_provenance_version FROM code_items WHERE id=$1",
            items[0]["id"],
        )
        assert tuple(provenance)[:2] == ("bound", "system:risk-auto")
        assert provenance["freeze_provenance_version"] == 1 and len(provenance["freeze_reason"]) <= 200
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await _assert_sqlstate(
                runtime.execute(
                    "UPDATE interception_records SET action_taken=NULL WHERE id=$1",
                    interception_id,
                ),
                "55000",
            )
        before_counts = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM risk_alerts WHERE id=$1) alerts,"
            "(SELECT count(*) FROM platform_audit_log WHERE id=$2) audits",
            alert_id,
            risk_audit_id,
        )
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM freeze_code_item_for_risk($1,$2,$3,$4,$5)",
                ids["tenant"],
                interception_id,
                items[0]["id"],
                uuid.uuid4(),
                uuid.uuid4(),
            ),
            "22023",
        )
        assert await owner.fetchval("SELECT count(*) FROM risk_alerts WHERE id=$1", alert_id) == before_counts["alerts"]
        assert (
            await owner.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", risk_audit_id)
            == before_counts["audits"]
        )

        recovery_audit = uuid.uuid4()
        owned_audits.append(recovery_audit)
        await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
            ids["tenant"],
            auth_session_id,
            recovery_audit,
            items[0]["id"],
        )

        blocker = owner.transaction()
        await blocker.start()
        await owner.execute("SELECT id FROM code_batches WHERE id=$1 FOR UPDATE", batch_id)
        first_scan_rows = await asyncio.wait_for(
            asyncio.gather(
                _runtime_call(
                    runtime,
                    ids["tenant"],
                    "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,'Mozilla/5.0','browser',NULL)",
                    ids["tenant"],
                    items[0]["public_id"],
                    uuid.uuid4(),
                ),
                _first_scan_on_new_connection(runtime_dsn, ids["tenant"], items[1]["public_id"]),
            ),
            timeout=2,
        )
        await blocker.rollback()
        assert all(row["first_scan"] for row in first_scan_rows)

        generic_audit_id = uuid.uuid4()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await _assert_sqlstate(
                runtime.fetchrow(
                    "SELECT * FROM append_authenticated_audit_event($1,$2,$3,'code_freeze',$4,'{}'::jsonb)",
                    generic_audit_id,
                    auth_session_id,
                    str(ids["tenant"]),
                    f"code_item:{items[0]['public_id']}",
                ),
                "42501",
            )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", generic_audit_id)

        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        frozen_batch = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'freeze','batch freeze')",
            ids["tenant"],
            auth_session_id,
            audit_id,
            batch_id,
        )
        assert frozen_batch["affected_item_count"] == 2
        for item in items:
            audit_id = uuid.uuid4()
            owned_audits.append(audit_id)
            await _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
                ids["tenant"],
                auth_session_id,
                audit_id,
                item["id"],
            )

        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'void','single void')",
            ids["tenant"],
            auth_session_id,
            audit_id,
            items[0]["id"],
        )
        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        recall_conn = await asyncpg.connect(owner_dsn)
        lifecycle_tx = runtime.transaction()
        await lifecycle_tx.start()
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        bound_before_recall = await runtime.fetchrow(
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'bind',NULL)",
            ids["tenant"],
            auth_session_id,
            audit_id,
            items[1]["id"],
        )
        recall_task = asyncio.create_task(
            recall_conn.execute(
                "UPDATE production_batches SET status='recalled',recall_reason='acceptance recall',"
                "recalled_at=now(),recalled_by=$1 WHERE id=$2",
                str(ids["account"]),
                ids["production_batch"],
            )
        )
        await asyncio.sleep(0.1)
        assert not recall_task.done(), "recall must serialize behind the lifecycle PB lock"
        await lifecycle_tx.commit()
        recall_result = await asyncio.wait_for(recall_task, timeout=3)
        await recall_conn.close()
        assert recall_result == "UPDATE 1"
        assert bound_before_recall["current_status"] == "bound"
        assert await owner.fetchval("SELECT frozen_from_status FROM code_items WHERE id=$1", items[1]["id"]) == "bound"
        audit_id = uuid.uuid4()
        owned_audits.append(audit_id)
        voided_batch = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'void','batch void')",
            ids["tenant"],
            auth_session_id,
            audit_id,
            batch_id,
        )
        assert voided_batch["affected_item_count"] == 1
        repeated_void_audit = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'void','repeat')",
                ids["tenant"],
                auth_session_id,
                repeated_void_audit,
                items[1]["id"],
            ),
            "22023",
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", repeated_void_audit
        )

        recalled_ids = await _seed_catalog(owner, "recalled-first")
        recalled_receipt = await _insert_receipt(owner, recalled_ids)
        recalled_batch = await _insert_batch(owner, recalled_ids, recalled_receipt)
        await _insert_items(owner, recalled_ids["tenant"], recalled_batch, 2)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", recalled_batch)
        await _insert_manifest_and_deliver(owner, recalled_ids, recalled_batch, row_count=2)
        recalled_session = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            recalled_session,
            recalled_ids["account"],
            recalled_ids["tenant"],
            uuid.uuid4().hex,
        )
        await owner.execute(
            "UPDATE production_batches SET status='recalled',recall_reason='recall first',"
            "recalled_at=now(),recalled_by=$1 WHERE id=$2",
            str(recalled_ids["account"]),
            recalled_ids["production_batch"],
        )
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                recalled_ids["tenant"],
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                recalled_ids["tenant"],
                recalled_session,
                uuid.uuid4(),
                recalled_batch,
            ),
            "22023",
        )
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                recalled_ids["tenant"],
                recalled_session,
                uuid.uuid4(),
                recalled_batch,
            ),
            "42501",
        )

        blocked_downgrade = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "Cannot discard risk interception code-item evidence" in (
            blocked_downgrade.stdout + blocked_downgrade.stderr
        )
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        await owner.execute("DELETE FROM interception_records WHERE id=$1", interception_id)
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='code_items' AND column_name='frozen_from_status')"
        )
        assert await owner.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        _alembic(migrated_pg_url, "upgrade", "head")
        _alembic(migrated_pg_url, "check")
        for fixture in (ids, recalled_ids):
            await _purge_owned_delivery_fixture(owner, fixture["tenant"])
        await owner.execute(
            "DELETE FROM risk_alerts WHERE tenant_id=ANY($1::uuid[])", [ids["tenant"], recalled_ids["tenant"]]
        )
        await owner.execute(
            "DELETE FROM interception_records WHERE tenant_id=ANY($1::uuid[])",
            [ids["tenant"], recalled_ids["tenant"]],
        )
        await owner.execute(
            "DELETE FROM risk_rules WHERE tenant_id=ANY($1::uuid[])", [ids["tenant"], recalled_ids["tenant"]]
        )
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=ANY($1::text[])",
            [str(ids["tenant"]), str(recalled_ids["tenant"])],
        )
        await owner.execute(
            "DELETE FROM auth_sessions WHERE tenant_id=ANY($1::uuid[])",
            [ids["tenant"], recalled_ids["tenant"]],
        )
        for fixture in (ids, recalled_ids):
            await owner.execute("DELETE FROM production_batches WHERE tenant_id=$1", fixture["tenant"])
    finally:
        await runtime.close()
        await owner.close()


async def test_acting_agency_lifecycle_authority_and_audit_are_target_bound(migrated_pg_url: str) -> None:
    """An acting agency needs a live codes grant and cannot forge lifecycle audit identity."""

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    revoker = await asyncpg.connect(owner_dsn)
    target_ids = await _seed_catalog(owner, "acting-code-target")
    other_ids = await _seed_catalog(owner, "acting-code-other")
    agency_ids = await _seed_catalog(owner, "acting-code-agency")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency_ids["tenant"])
    target_receipt = await _insert_receipt(owner, target_ids)
    target_batch = await _insert_batch(owner, target_ids, target_receipt)
    await _insert_items(owner, target_ids["tenant"], target_batch, 2)
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", target_batch)
    await _insert_manifest_and_deliver(owner, target_ids, target_batch, row_count=2)
    other_receipt = await _insert_receipt(owner, other_ids)
    other_batch = await _insert_batch(owner, other_ids, other_receipt)
    await _insert_items(owner, other_ids["tenant"], other_batch, 2)
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", other_batch)
    await _insert_manifest_and_deliver(owner, other_ids, other_batch, row_count=2)
    target_items = await owner.fetch(
        "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id::text",
        target_ids["tenant"],
        target_batch,
    )
    agency_session = uuid.uuid4()
    authorization_id = uuid.uuid4()
    owned_audits: list[uuid.UUID] = []
    owned_alerts: list[uuid.UUID] = []
    revocation_tx = revoker.transaction()
    revocation_tx_active = False

    async def insert_authorization(scope: str, expires_sql: str = "now()+interval '1 hour'") -> None:
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,created_at,updated_at) "
            f"VALUES($1,$2,$3,$4::jsonb,'active',$5,now(),{expires_sql},now(),now())",
            authorization_id,
            agency_ids["tenant"],
            target_ids["tenant"],
            scope,
            target_ids["account"],
        )

    async def assert_rejected_item_freeze(expected_sqlstate: str = "42501") -> None:
        rejected_audit = uuid.uuid4()
        before = await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", target_items[0]["id"])
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                target_ids["tenant"],
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'freeze','agency rejection')",
                target_ids["tenant"],
                agency_session,
                rejected_audit,
                target_items[0]["id"],
            ),
            expected_sqlstate,
        )
        assert await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", target_items[0]["id"]) == before
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)",
            rejected_audit,
        )

    try:
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            agency_session,
            agency_ids["account"],
            agency_ids["tenant"],
            uuid.uuid4().hex,
        )

        # A live agency session without the exact codes scope is rejected with
        # no item or audit drift.
        await insert_authorization('["pages"]')
        await assert_rejected_item_freeze()
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await insert_authorization('["codes"]')

        activation_audit = uuid.uuid4()
        owned_audits.append(activation_audit)
        activated = await _runtime_call(
            runtime,
            target_ids["tenant"],
            "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
            target_ids["tenant"],
            agency_session,
            activation_audit,
            target_batch,
        )
        assert activated["current_status"] == "activated" and activated["affected_item_count"] == 2
        activation_event = await owner.fetchrow(
            "SELECT operator_id,target_tenant_id,action,resource FROM platform_audit_log WHERE id=$1",
            activation_audit,
        )
        assert tuple(activation_event) == (
            str(agency_ids["account"]),
            str(target_ids["tenant"]),
            "code_activate",
            f"code_batch:{target_batch}",
        )

        freeze_audit = uuid.uuid4()
        recover_audit = uuid.uuid4()
        owned_audits.extend((freeze_audit, recover_audit))
        await _runtime_call(
            runtime,
            target_ids["tenant"],
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'freeze','agency review')",
            target_ids["tenant"],
            agency_session,
            freeze_audit,
            target_items[0]["id"],
        )
        recovered = await _runtime_call(
            runtime,
            target_ids["tenant"],
            "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
            target_ids["tenant"],
            agency_session,
            recover_audit,
            target_items[0]["id"],
        )
        assert recovered["current_status"] == "activated"
        recover_event = await owner.fetchrow(
            "SELECT operator_id,target_tenant_id,action,resource FROM platform_audit_log WHERE id=$1",
            recover_audit,
        )
        assert tuple(recover_event) == (
            str(agency_ids["account"]),
            str(target_ids["tenant"]),
            "code_recover",
            f"code_item:{target_items[0]['public_id']}",
        )

        resolved_alert = uuid.uuid4()
        resolved_audit = uuid.uuid4()
        owned_alerts.append(resolved_alert)
        owned_audits.append(resolved_audit)
        await owner.execute(
            "INSERT INTO risk_alerts "
            "(id,tenant_id,alert_type,public_id,code_item_id,detail,resolved,created_at,updated_at) "
            "VALUES($1,$2,'suspected_copy',$3,$4,'resolved acceptance alert',true,now(),now())",
            resolved_alert,
            target_ids["tenant"],
            target_items[0]["public_id"],
            target_items[0]["id"],
        )
        resolved_event = await _runtime_call(
            runtime,
            target_ids["tenant"],
            "SELECT * FROM append_authenticated_audit_event($1,$2,$3,'risk_alert_resolved',$4,'{}'::jsonb)",
            resolved_audit,
            agency_session,
            str(target_ids["tenant"]),
            f"risk_alert:{resolved_alert}",
        )
        assert resolved_event["resolved_operator_id"] == str(agency_ids["account"])
        assert await owner.fetchval(
            "SELECT target_tenant_id FROM platform_audit_log WHERE id=$1",
            resolved_audit,
        ) == str(target_ids["tenant"])
        forged_audit = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                target_ids["tenant"],
                "SELECT * FROM append_authenticated_audit_event($1,$2,$3,'risk_alert_resolved',$4,'{}'::jsonb)",
                forged_audit,
                agency_session,
                str(target_ids["tenant"]),
                f"risk_alert:{uuid.uuid4()}",
            ),
            "23503",
        )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", forged_audit)

        # An elapsed authorization is independently rejected. The row starts
        # valid, then crosses its timestamp boundary before the lifecycle call.
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await insert_authorization('["codes"]', "clock_timestamp()+interval '200 milliseconds'")
        await asyncio.sleep(0.3)
        await assert_rejected_item_freeze()

        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await insert_authorization('["codes"]')
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        await assert_rejected_item_freeze()

        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await insert_authorization('["codes"]')
        other_audit = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                other_ids["tenant"],
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                other_ids["tenant"],
                agency_session,
                other_audit,
                other_batch,
            ),
            "42501",
        )
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", other_batch) == "delivered"
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", other_audit)
        mismatch_audit = uuid.uuid4()
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                target_ids["tenant"],
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                other_ids["tenant"],
                agency_session,
                mismatch_audit,
                other_batch,
            ),
            "42501",
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)",
            mismatch_audit,
        )

        # A revoker linearized first under the canonical pair authority lock;
        # the waiting lifecycle call must re-read the row after acquiring it.
        await revocation_tx.start()
        revocation_tx_active = True
        await revoker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text || ':' || $2::text,0))",
            str(agency_ids["tenant"]),
            str(target_ids["tenant"]),
        )
        await revoker.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        concurrent_audit = uuid.uuid4()
        concurrent_freeze = asyncio.create_task(
            _runtime_call(
                runtime,
                target_ids["tenant"],
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'freeze','concurrent revoke')",
                target_ids["tenant"],
                agency_session,
                concurrent_audit,
                target_items[0]["id"],
            )
        )
        await asyncio.sleep(0.1)
        assert not concurrent_freeze.done(), "lifecycle must wait behind the authorization pair lock"
        await revocation_tx.commit()
        revocation_tx_active = False
        await _assert_sqlstate(asyncio.wait_for(concurrent_freeze, timeout=3), "42501")
        assert (
            await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", target_items[0]["id"])
            == "activated"
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)",
            concurrent_audit,
        )
    finally:
        if revocation_tx_active:
            await revocation_tx.rollback()
        await owner.execute("DELETE FROM risk_alerts WHERE id=ANY($1::uuid[])", owned_alerts)
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", agency_session)
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=ANY($1::text[])",
            [str(target_ids["tenant"]), str(other_ids["tenant"]), str(agency_ids["tenant"])],
        )
        for fixture in (target_ids, other_ids, agency_ids):
            await _purge_owned_delivery_fixture(owner, fixture["tenant"])
            await owner.execute("DELETE FROM production_batches WHERE tenant_id=$1", fixture["tenant"])
        await revoker.close()
        await runtime.close()
        await owner.close()


async def _first_scan_on_new_connection(
    runtime_dsn: str,
    tenant_id: uuid.UUID,
    public_id: str,
) -> asyncpg.Record:
    conn = await asyncpg.connect(runtime_dsn)
    try:
        return await _runtime_call(
            conn,
            tenant_id,
            "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,'Mozilla/5.0','browser',NULL)",
            tenant_id,
            public_id,
            uuid.uuid4(),
        )
    finally:
        await conn.close()
