"""Real PostgreSQL contract for function-only public scan evidence."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from tests.test_acceptance.test_code_batch_delivery_contract import (
    _insert_batch,
    _insert_items,
    _insert_manifest_and_deliver,
    _insert_receipt,
    _purge_owned_delivery_fixture,
    _seed_catalog,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "d297eb0518da"


async def _runtime_scan(
    dsn: str,
    tenant_id: uuid.UUID,
    public_id: str,
    event_id: uuid.UUID,
) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
            return await conn.fetchrow(
                "SELECT * FROM public.record_public_code_scan($1,$2,$3,$4,'Mozilla/5.0','browser',NULL)",
                tenant_id,
                public_id,
                event_id,
                "a" * 64,
            )
    finally:
        await conn.close()


async def _assert_sqlstate(coro, sqlstate: str) -> None:
    with pytest.raises(asyncpg.PostgresError) as raised:
        await coro
    assert raised.value.sqlstate == sqlstate


async def test_public_scan_facts_are_referential_atomic_and_function_only(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "public-scan-authority")
    receipt_id = await _insert_receipt(owner, ids)
    batch_id = await _insert_batch(owner, ids, receipt_id, quantity=2, expected_item_count=2)
    await _insert_items(owner, ids["tenant"], batch_id, 2)
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
    await _insert_manifest_and_deliver(owner, ids, batch_id, row_count=2)
    async with owner.transaction():
        await owner.execute(
            "UPDATE code_items SET status='activated',activated_at=now() WHERE code_batch_id=$1",
            batch_id,
        )
        await owner.execute("UPDATE code_batches SET status='activated' WHERE id=$1", batch_id)
    items = await owner.fetch(
        "SELECT id,public_id FROM code_items WHERE code_batch_id=$1 ORDER BY public_id",
        batch_id,
    )

    try:
        assert await owner.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conname='fk_scan_events_tenant_public_id' AND conrelid='scan_events'::regclass"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege("
            "'yimatong_app','public.record_public_code_scan(uuid,text,uuid,text,text,text,text)','EXECUTE')"
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','public.mark_code_item_first_scanned(uuid,text)','EXECUTE')"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.scan_events',$1)", privilege
            )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                await runtime.execute(
                    "INSERT INTO scan_events "
                    "(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit) "
                    "VALUES($1,$2,$3,now(),false,false)",
                    uuid.uuid4(),
                    ids["tenant"],
                    items[0]["public_id"],
                )

        first_event_id = uuid.uuid4()
        first = await _runtime_scan(runtime_dsn, ids["tenant"], items[0]["public_id"], first_event_id)
        assert first["scan_event_id"] == first_event_id
        assert first["first_scan"] is True
        assert first["is_valid_visit"] is True
        assert await owner.fetchval(
            "SELECT first_scanned_at=scan_time FROM code_items item JOIN scan_events event "
            "ON event.tenant_id=item.tenant_id AND event.public_id=item.public_id "
            "WHERE event.id=$1",
            first_event_id,
        )

        # Inject a database failure at the event insert, after the function has
        # updated first_scanned_at, and prove the statement is fully atomic.
        await owner.execute(
            "CREATE FUNCTION acceptance_reject_public_scan() RETURNS trigger "
            "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION USING ERRCODE='23514', "
            "MESSAGE='acceptance scan rejection'; END$$"
        )
        await owner.execute(
            "CREATE TRIGGER acceptance_reject_public_scan "
            "BEFORE INSERT ON scan_events FOR EACH ROW EXECUTE FUNCTION acceptance_reject_public_scan()"
        )
        await _assert_sqlstate(
            _runtime_scan(runtime_dsn, ids["tenant"], items[1]["public_id"], uuid.uuid4()),
            "23514",
        )
        await owner.execute("DROP TRIGGER acceptance_reject_public_scan ON scan_events")
        await owner.execute("DROP FUNCTION acceptance_reject_public_scan()")
        assert await owner.fetchval("SELECT first_scanned_at IS NULL FROM code_items WHERE id=$1", items[1]["id"])

        concurrent = await asyncio.wait_for(
            asyncio.gather(
                _runtime_scan(runtime_dsn, ids["tenant"], items[1]["public_id"], uuid.uuid4()),
                _runtime_scan(runtime_dsn, ids["tenant"], items[1]["public_id"], uuid.uuid4()),
            ),
            timeout=10,
        )
        assert sorted(row["first_scan"] for row in concurrent) == [False, True]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM scan_events WHERE tenant_id=$1 AND public_id=$2",
                ids["tenant"],
                items[1]["public_id"],
            )
            == 2
        )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                await runtime.fetchrow(
                    "SELECT * FROM public.record_public_code_scan($1,$2,$3,NULL,'Mozilla/5.0','browser',NULL)",
                    uuid.uuid4(),
                    items[0]["public_id"],
                    uuid.uuid4(),
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                await runtime.execute("UPDATE scan_events SET is_valid_visit=false WHERE id=$1", first_event_id)
        await _assert_sqlstate(
            owner.execute(
                "INSERT INTO scan_events "
                "(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit) "
                "VALUES($1,$2,'MISSING-CODE',now(),false,false)",
                uuid.uuid4(),
                ids["tenant"],
            ),
            "23503",
        )

        # Pause a scan after it has taken the PB share lock but before it can
        # lock the target item. A concurrent recall must wait, then linearize
        # after the valid scan; a later scan observes the recalled PB as invalid.
        blocker = owner.transaction()
        await blocker.start()
        recall_conn = await asyncpg.connect(owner_dsn)
        scan_task: asyncio.Task[asyncpg.Record] | None = None
        recall_task: asyncio.Task[str] | None = None
        try:
            await owner.execute("SELECT id FROM code_items WHERE id=$1 FOR UPDATE", items[0]["id"])
            scan_task = asyncio.create_task(
                _runtime_scan(runtime_dsn, ids["tenant"], items[0]["public_id"], uuid.uuid4())
            )
            for _ in range(50):
                waiting_scan = await recall_conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND query LIKE 'SELECT * FROM public.record_public_code_scan%' "
                    "AND wait_event_type='Lock')"
                )
                if waiting_scan:
                    break
                await asyncio.sleep(0.02)
            assert waiting_scan
            recall_task = asyncio.create_task(
                recall_conn.execute(
                    "UPDATE production_batches SET status='recalled',recall_reason='scan authority recall',"
                    "recalled_at=now(),recalled_by=$1 WHERE id=$2",
                    str(ids["account"]),
                    ids["production_batch"],
                )
            )
            await asyncio.sleep(0.1)
            assert not recall_task.done()
            await blocker.rollback()
            scan_before_recall = await asyncio.wait_for(scan_task, timeout=5)
            assert scan_before_recall["is_valid_visit"] is True
            assert await asyncio.wait_for(recall_task, timeout=5) == "UPDATE 1"
            scan_after_recall = await _runtime_scan(
                runtime_dsn,
                ids["tenant"],
                items[0]["public_id"],
                uuid.uuid4(),
            )
            assert scan_after_recall["is_valid_visit"] is False
        finally:
            if scan_task is not None and not scan_task.done():
                scan_task.cancel()
                await asyncio.gather(scan_task, return_exceptions=True)
            if recall_task is not None and not recall_task.done():
                recall_task.cancel()
                await asyncio.gather(recall_task, return_exceptions=True)
            if owner.is_in_transaction():
                await blocker.rollback()
            await recall_conn.close()
    finally:
        await runtime.close()
        await _purge_owned_delivery_fixture(owner, ids["tenant"])
        await owner.close()


async def test_scan_authority_round_trip_preserves_future_partition_contract(migrated_pg_url: str) -> None:
    """Future partitions inherit the FK and the three-stage contract round-trips cleanly."""

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    partition = "scan_events_2099_01"
    try:
        assert (
            await owner.fetchval("SELECT public.create_secure_scan_events_partition('2099-01-01'::date)") == partition
        )
        assert await owner.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conname='fk_scan_events_tenant_public_id' "
            "AND conrelid=to_regclass('public.' || $1)",
            partition,
        )

        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
        assert not await owner.fetchval(
            "SELECT to_regprocedure('public.record_public_code_scan(uuid,text,uuid,text,text,text,text)') IS NOT NULL"
        )
        assert await owner.fetchval("SELECT has_table_privilege('yimatong_app','public.scan_events','INSERT')")

        await owner.execute(
            "CREATE UNIQUE INDEX uq_code_items_tenant_public_id ON public.code_items (public_id, tenant_id)"
        )
        await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "upgrade",
            "u4a1e2f3a4b5",
            succeeds=False,
        )
        assert await owner.fetchval("SELECT pg_get_indexdef(to_regclass('public.uq_code_items_tenant_public_id'))") == (
            "CREATE UNIQUE INDEX uq_code_items_tenant_public_id ON public.code_items USING btree (public_id, tenant_id)"
        )
        await owner.execute("DROP INDEX public.uq_code_items_tenant_public_id")

        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
        assert await owner.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conname='fk_scan_events_tenant_public_id' "
            "AND conrelid=to_regclass('public.' || $1)",
            partition,
        )
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','public.scan_events','INSERT')")
        await asyncio.to_thread(_alembic, migrated_pg_url, "check")
    finally:
        await owner.execute(f"DROP TABLE IF EXISTS public.{partition}")
        await owner.close()
