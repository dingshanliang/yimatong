"""Online rollout and rollback contract for delivery retry authority."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from tests.test_acceptance.test_campaign_authority_rollout import _isolated_campaign_rollout_database
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

INDEX_REVISION = "u6k0c1d2e3f4"
HEAD_REVISION = "u6k1d2e3f4a5"
PARENT_REVISION = "u6b5c6d7e8f9"
DEEP_PARENT_REVISION = "649cdfd94581"
INDEX_NAME = "uq_benefit_deliveries_tenant_id_id"
LEGACY_RETRY_INDEX = "ix_campaign_delivery_legacy_retry"
DOWNGRADE_INDEX = "uq_benefit_deliveries_tenant_id_id_downgrade"
CALLBACK_SIGNATURE = "public.settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
DELIVERY_RESULT_SIGNATURE = (
    "public.record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)"
)


async def test_populated_parent_upgrade_and_clean_roundtrip(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "delivery-retry-rollout")
            connector_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'legacy retry','generic_http','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            delivery_ids = [uuid.uuid4() for _ in range(20)]
            await owner.executemany(
                "INSERT INTO benefit_deliveries(id,tenant_id,connector_id,consumer_id,benefit_type,benefit_config,"
                "status,retry_count,max_retries,next_retry_at,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'coupon','{}','pending',0,5,now(),now(),now())",
                [
                    (delivery_id, ids["tenant"], connector_id, f"legacy-{index}")
                    for index, delivery_id in enumerate(delivery_ids)
                ],
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
            assert await owner.fetchval(
                "SELECT count(*) FROM benefit_deliveries WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
                ids["tenant"],
                delivery_ids,
            ) == len(delivery_ids)
            constraint = await owner.fetchrow(
                "SELECT c.contype::text AS type,i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) definition "
                "FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid WHERE c.conname=$1",
                INDEX_NAME,
            )
            assert constraint is not None and dict(constraint) == {
                "type": "u",
                "indisvalid": True,
                "indisunique": True,
                "definition": "CREATE UNIQUE INDEX uq_benefit_deliveries_tenant_id_id "
                "ON public.benefit_deliveries USING btree (tenant_id, id)",
            }
            assert await owner.fetchval(
                "SELECT confdeltype='r' FROM pg_constraint "
                "WHERE conname='fk_campaign_callback_attempts_tenant_delivery'"
            )
            assert await owner.fetchval(
                "SELECT indisvalid AND NOT indisunique FROM pg_index WHERE indexrelid=$1::regclass",
                f"public.{LEGACY_RETRY_INDEX}",
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await asyncio.to_thread(_alembic, database_url, "check")


async def test_invalid_exact_delivery_identity_index_is_rebuilt(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(f"CREATE UNIQUE INDEX {INDEX_NAME} ON benefit_deliveries(tenant_id,id)")
            await owner.execute(
                f"CREATE INDEX {LEGACY_RETRY_INDEX} ON benefit_deliveries(tenant_id,next_retry_at,id) "
                "WHERE campaign_outbox_id IS NULL AND status='pending'"
            )
            await owner.execute(f"CREATE UNIQUE INDEX {DOWNGRADE_INDEX} ON benefit_deliveries(tenant_id,id)")
            for index_name in (INDEX_NAME, LEGACY_RETRY_INDEX, DOWNGRADE_INDEX):
                await owner.execute(
                    f"UPDATE pg_index SET indisvalid=false WHERE indexrelid='public.{index_name}'::regclass"
                )
                assert not await owner.fetchval(
                    f"SELECT indisvalid FROM pg_index WHERE indexrelid='public.{index_name}'::regclass"
                )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval(
                f"SELECT indisvalid AND indisunique FROM pg_index WHERE indexrelid='public.{INDEX_NAME}'::regclass"
            )
            assert await owner.fetchval(
                f"SELECT indisvalid AND NOT indisunique FROM pg_index "
                f"WHERE indexrelid='public.{LEGACY_RETRY_INDEX}'::regclass"
            )
            assert await owner.fetchval(
                f"SELECT indisvalid AND indisunique FROM pg_index WHERE indexrelid='public.{DOWNGRADE_INDEX}'::regclass"
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")


async def test_unexpected_same_name_delivery_index_fails_closed(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(f"CREATE UNIQUE INDEX {INDEX_NAME} ON benefit_deliveries(id,tenant_id)")
            definition_before = await owner.fetchval(f"SELECT pg_get_indexdef('public.{INDEX_NAME}'::regclass)")
        finally:
            await owner.close()
        blocked = await asyncio.to_thread(
            _alembic,
            database_url,
            "upgrade",
            INDEX_REVISION,
            succeeds=False,
        )
        assert "refusing to replace unexpected delivery authority index" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
            assert await owner.fetchval(f"SELECT pg_get_indexdef('public.{INDEX_NAME}'::regclass)") == definition_before
            await owner.execute(f"DROP INDEX public.{INDEX_NAME}")
        finally:
            await owner.close()


async def test_concurrent_index_rollout_does_not_block_populated_table_writer(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        seed = await asyncpg.connect(owner_dsn)
        blocker = await asyncpg.connect(owner_dsn)
        writer = await asyncpg.connect(owner_dsn)
        migration_task: asyncio.Task[object] | None = None
        try:
            ids = await _seed_catalog(seed, "delivery-retry-concurrent-index")
            connector_id = uuid.uuid4()
            delivery_id = uuid.uuid4()
            await seed.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'concurrent retry','generic_http','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await seed.execute(
                "INSERT INTO benefit_deliveries(id,tenant_id,connector_id,consumer_id,benefit_type,benefit_config,"
                "status,retry_count,max_retries,next_retry_at,created_at,updated_at) "
                "VALUES($1,$2,$3,'held-row','coupon','{}','pending',0,5,now(),now(),now())",
                delivery_id,
                ids["tenant"],
                connector_id,
            )
            blocker_tx = blocker.transaction()
            await blocker_tx.start()
            await blocker.execute("UPDATE benefit_deliveries SET updated_at=now() WHERE id=$1", delivery_id)
            migration_task = asyncio.create_task(asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION))
            for _ in range(100):
                if await seed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                    "AND query ILIKE '%INDEX CONCURRENTLY%benefit_deliveries%' AND pid<>pg_backend_pid())"
                ):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("concurrent delivery index build was not observed")
            await writer.execute("SET statement_timeout='1500ms'")
            await writer.execute(
                "INSERT INTO benefit_deliveries(id,tenant_id,connector_id,consumer_id,benefit_type,benefit_config,"
                "status,retry_count,max_retries,next_retry_at,created_at,updated_at) "
                "VALUES($1,$2,$3,'concurrent-writer','coupon','{}','pending',0,5,now(),now(),now())",
                uuid.uuid4(),
                ids["tenant"],
                connector_id,
            )
            await blocker_tx.commit()
            await migration_task
            migration_task = None
        finally:
            if migration_task is not None and not migration_task.done():
                await blocker.execute("ROLLBACK")
                await migration_task
            await asyncio.gather(seed.close(), blocker.close(), writer.close())
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")


async def test_deep_downgrade_blocks_before_u06c_head_or_catalog_drift(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "delivery-retry-deep-downgrade")
            receipt_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,result,recorded_at) "
                "VALUES($1,$2,'evaluate',$3,$4,'{}',now())",
                receipt_id,
                ids["tenant"],
                f"risk:v1:{uuid.uuid4().hex}",
                "d" * 64,
            )
            head_before = await owner.fetchval("SELECT version_num FROM alembic_version")
            receipt_before = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
                "FROM risk_action_receipts receipt WHERE id=$1",
                receipt_id,
            )
            function_before = await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", CALLBACK_SIGNATURE)
            indexes_before = await owner.fetch(
                "SELECT c.relname,pg_get_indexdef(i.indexrelid) definition FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=ANY($1::text[]) ORDER BY c.relname",
                [INDEX_NAME, LEGACY_RETRY_INDEX],
            )
        finally:
            await owner.close()

        blocked = await asyncio.to_thread(
            _alembic,
            database_url,
            "downgrade",
            DEEP_PARENT_REVISION,
            succeeds=False,
        )
        assert "risk action receipts are immutable facts" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == head_before == HEAD_REVISION
            assert (
                await owner.fetchrow(
                    "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
                    "FROM risk_action_receipts receipt WHERE id=$1",
                    receipt_id,
                )
                == receipt_before
            )
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", CALLBACK_SIGNATURE)
                == function_before
            )
            assert (
                await owner.fetch(
                    "SELECT c.relname,pg_get_indexdef(i.indexrelid) definition FROM pg_index i "
                    "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=ANY($1::text[]) ORDER BY c.relname",
                    [INDEX_NAME, LEGACY_RETRY_INDEX],
                )
                == indexes_before
            )
        finally:
            await owner.close()


async def test_cutover_lock_timeout_is_retryable_without_partial_ddl(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        blocker = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "delivery-cutover-lock-timeout")
            connector_id, delivery_id = uuid.uuid4(), uuid.uuid4()
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'cutover lock','generic_http','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO benefit_deliveries(id,tenant_id,connector_id,consumer_id,benefit_type,benefit_config,"
                "status,retry_count,max_retries,next_retry_at,created_at,updated_at) "
                "VALUES($1,$2,$3,'lock-row','coupon','{}','pending',0,5,now(),now(),now())",
                delivery_id,
                ids["tenant"],
                connector_id,
            )
            row_before = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(delivery)::text) AS digest "
                "FROM benefit_deliveries delivery WHERE id=$1",
                delivery_id,
            )
            index_before = await owner.fetchval(f"SELECT pg_get_indexdef('public.{INDEX_NAME}'::regclass)")
            legacy_function_before = await owner.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure($1))", DELIVERY_RESULT_SIGNATURE
            )
            blocker_tx = blocker.transaction()
            await blocker_tx.start()
            await blocker.execute("LOCK TABLE benefit_deliveries IN ACCESS SHARE MODE")
            blocked = await asyncio.to_thread(
                _alembic,
                database_url,
                "upgrade",
                HEAD_REVISION,
                succeeds=False,
            )
            assert "lock timeout" in f"{blocked.stdout}\n{blocked.stderr}".lower()
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == INDEX_REVISION
            assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname=$1)", INDEX_NAME)
            assert await owner.fetchval("SELECT to_regclass('public.campaign_delivery_callback_attempts') IS NULL")
            assert await owner.fetchval("SELECT pg_get_indexdef($1::regclass)", f"public.{INDEX_NAME}") == index_before
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", DELIVERY_RESULT_SIGNATURE)
                == legacy_function_before
            )
            assert (
                await owner.fetchrow(
                    "SELECT xmin::text AS xmin,md5(row_to_json(delivery)::text) AS digest "
                    "FROM benefit_deliveries delivery WHERE id=$1",
                    delivery_id,
                )
                == row_before
            )
            await blocker_tx.commit()
        finally:
            await asyncio.gather(owner.close(), blocker.close())
        await asyncio.to_thread(_alembic, database_url, "upgrade", HEAD_REVISION)
        await asyncio.to_thread(_alembic, database_url, "check")


async def test_cutover_downgrade_lock_timeout_preserves_head_and_retries(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        blocker = await asyncpg.connect(owner_dsn)
        try:
            function_before = await owner.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure($1))", DELIVERY_RESULT_SIGNATURE
            )
            constraint_before = await owner.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=$1", INDEX_NAME
            )
            blocker_tx = blocker.transaction()
            await blocker_tx.start()
            await blocker.execute("LOCK TABLE benefit_deliveries IN ACCESS SHARE MODE")
            blocked = await asyncio.to_thread(
                _alembic,
                database_url,
                "downgrade",
                INDEX_REVISION,
                succeeds=False,
            )
            assert "lock timeout" in f"{blocked.stdout}\n{blocked.stderr}".lower()
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
            assert (
                await owner.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=$1", INDEX_NAME)
                == constraint_before
            )
            assert await owner.fetchval("SELECT to_regclass('public.campaign_delivery_callback_attempts') IS NOT NULL")
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", DELIVERY_RESULT_SIGNATURE)
                == function_before
            )
            await blocker_tx.commit()
        finally:
            await asyncio.gather(owner.close(), blocker.close())
        await asyncio.to_thread(_alembic, database_url, "downgrade", INDEX_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval(
                f"SELECT indisvalid AND indisunique FROM pg_index WHERE indexrelid='public.{INDEX_NAME}'::regclass"
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", HEAD_REVISION)
        await asyncio.to_thread(_alembic, database_url, "check")
