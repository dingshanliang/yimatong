"""Recovery gates for independently committed U08D concurrent indexes."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
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

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "u8d1f2a3b4c5"
INDEX_REVISION = "u8d2a3b4c5d6"
INDEX_NAMES = (
    "uq_webhook_endpoints_tenant_id_id_idx",
    "uq_webhook_domain_events_tenant_id_id_idx",
    "ix_webhook_domain_events_tenant_created",
    "ix_webhook_domain_events_pending",
    "uq_webhook_deliveries_domain_endpoint",
    "ix_webhook_deliveries_due",
)


def _owner_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _index_facts(conn: asyncpg.Connection, name: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT index_row.indisvalid,index_row.indisready,index_row.indisunique,"
        "pg_get_indexdef(index_row.indexrelid) definition FROM pg_index index_row "
        "JOIN pg_class class ON class.oid=index_row.indexrelid "
        "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
        "WHERE namespace.nspname='public' AND class.relname=$1",
        name,
    )


async def test_u8d2_partial_cic_is_exactly_repairable_and_wrong_shapes_are_preserved(
    migrated_pg_url: str,
) -> None:
    database_name = f"yimatong_acceptance_u8d2_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    owner: asyncpg.Connection | None = None
    locker: asyncpg.Connection | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(_alembic, database_url, "upgrade", PARENT_REVISION)
        owner = await asyncpg.connect(_owner_dsn(database_url))
        tenant_id, endpoint_id = uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'U08D CIC recovery',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"u8d-cic-{tenant_id.hex[-12:]}",
        )
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,tenant_id,url,events,secret,secret_ciphertext,secret_nonce,"
            "secret_key_id,config_version,enabled,batch_mode,batch_size,created_at,updated_at) "
            "VALUES($1,$2,'https://example.invalid/recovery','[\"campaign.active\"]','legacy-secret',"
            "$3,$4,'test-key-v1',1,true,false,100,now(),now())",
            endpoint_id,
            tenant_id,
            b"x" * 32,
            b"n" * 12,
        )
        row_digest = await owner.fetchval(
            "SELECT md5(row_to_json(endpoint)::text) FROM webhook_endpoints endpoint WHERE id=$1", endpoint_id
        )
        function_digest = await owner.fetchval(
            "SELECT md5(pg_get_functiondef('public.transition_campaign(uuid,uuid,uuid,uuid,text)'::regprocedure))"
        )

        # The first endpoint index commits before a real lock timeout blocks
        # the next table. Alembic must remain at the parent revision.
        locker = await asyncpg.connect(_owner_dsn(database_url))
        lock_tx = locker.transaction()
        await lock_tx.start()
        await locker.execute("LOCK TABLE webhook_domain_events IN ACCESS EXCLUSIVE MODE")
        failed = await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION, succeeds=False)
        assert "lock timeout" in (failed.stdout + failed.stderr).lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        first = await _index_facts(owner, INDEX_NAMES[0])
        assert first is not None and first["indisvalid"] and first["indisready"]
        assert (
            await owner.fetchval(
                "SELECT md5(row_to_json(endpoint)::text) FROM webhook_endpoints endpoint WHERE id=$1", endpoint_id
            )
            == row_digest
        )
        assert (
            await owner.fetchval(
                "SELECT md5(pg_get_functiondef('public.transition_campaign(uuid,uuid,uuid,uuid,text)'::regprocedure))"
            )
            == function_digest
        )
        await lock_tx.rollback()
        await locker.close()
        locker = None

        await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION)
        for name in INDEX_NAMES:
            facts = await _index_facts(owner, name)
            assert facts is not None and facts["indisvalid"] and facts["indisready"]
        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        assert all([await _index_facts(owner, name) is None for name in INDEX_NAMES])

        # A valid but wrong-shape object is never dropped or replaced.
        await owner.execute(f"CREATE UNIQUE INDEX {INDEX_NAMES[0]} ON webhook_endpoints(id,tenant_id)")
        wrong_valid = await _index_facts(owner, INDEX_NAMES[0])
        rejected = await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION, succeeds=False)
        assert "refusing to replace unexpected" in (rejected.stdout + rejected.stderr)
        assert await _index_facts(owner, INDEX_NAMES[0]) == wrong_valid
        await owner.execute(f"DROP INDEX {INDEX_NAMES[0]}")

        # A failed unique CIC leaves a real invalid wrong-shape shell. It is
        # also preserved rather than guessed away from its name.
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,tenant_id,url,events,secret,secret_ciphertext,secret_nonce,"
            "secret_key_id,config_version,enabled,batch_mode,batch_size,created_at,updated_at) "
            "VALUES($1,$2,'https://example.invalid/recovery','[\"campaign.active\"]','legacy-secret',"
            "$3,$4,'test-key-v1',1,true,false,100,now(),now())",
            uuid7(),
            tenant_id,
            b"y" * 32,
            b"m" * 12,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await owner.execute(f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX_NAMES[0]} ON webhook_endpoints(url)")
        wrong_invalid = await _index_facts(owner, INDEX_NAMES[0])
        assert wrong_invalid is not None and not wrong_invalid["indisvalid"]
        rejected = await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION, succeeds=False)
        assert "refusing to replace unexpected" in (rejected.stdout + rejected.stderr)
        assert await _index_facts(owner, INDEX_NAMES[0]) == wrong_invalid
        await owner.execute(f"DROP INDEX CONCURRENTLY {INDEX_NAMES[0]}")

        # Duplicate pre-cutover rows force an exact invalid shell at the fifth
        # index. Removing the bad row lets retry drop only that exact shell.
        domain_event_id = uuid7()
        delivery_ids = (uuid7(), uuid7())
        for delivery_id in delivery_ids:
            await owner.execute(
                "INSERT INTO webhook_deliveries(id,tenant_id,endpoint_id,event_id,domain_event_id,event_type,"
                "payload,status,retry_count,created_at,updated_at) VALUES($1,$2,$3,$4,$5,'campaign.active',"
                "'{}','pending',0,now(),now())",
                delivery_id,
                tenant_id,
                endpoint_id,
                str(uuid7()),
                domain_event_id,
            )
        duplicate_failure = await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION, succeeds=False)
        assert "could not create unique index" in (duplicate_failure.stdout + duplicate_failure.stderr).lower()
        invalid_exact = await _index_facts(owner, "uq_webhook_deliveries_domain_endpoint")
        assert invalid_exact is not None and not invalid_exact["indisvalid"]
        await owner.execute("DELETE FROM webhook_deliveries WHERE id=$1", delivery_ids[1])
        await asyncio.to_thread(_alembic, database_url, "upgrade", INDEX_REVISION)
        for name in INDEX_NAMES:
            facts = await _index_facts(owner, name)
            assert facts is not None and facts["indisvalid"] and facts["indisready"]

        await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
        assert all([await _index_facts(owner, name) is None for name in INDEX_NAMES])
        await owner.execute("DELETE FROM webhook_deliveries")
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        await asyncio.to_thread(_alembic, database_url, "check")
    finally:
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
