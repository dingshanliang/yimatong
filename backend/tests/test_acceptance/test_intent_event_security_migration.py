"""Independent PostgreSQL proof for the intent-event security migration."""

import os
import subprocess
import sys
import time
import uuid

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "a313fa710459"
INDEX_NAME = "uq_intent_events_tenant_client_event"


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert (result.returncode == 0) is succeeds, f"{result.stdout}\n{result.stderr}"
    return result


async def test_schema_safe_preflight_retry_and_downgrade(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    database_name = await conn.fetchval("SELECT current_database()")
    current_role = await conn.fetchval("SELECT current_user")
    quoted_database = await conn.fetchval("SELECT quote_ident($1)", database_name)
    quoted_role = await conn.fetchval("SELECT quote_ident($1)", current_role)
    duplicate_tenant = uuid.uuid4()
    duplicate_key = f"duplicate-{uuid.uuid4()}"
    try:
        before = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='public.intent_events'::regclass"
        )
        assert (before["relrowsecurity"], before["relforcerowsecurity"]) == (False, False)

        await conn.executemany(
            "INSERT INTO intent_events (id, tenant_id, event_type, client_event_id) VALUES ($1, $2, 'page_view', $3)",
            [(uuid.uuid4(), duplicate_tenant, duplicate_key), (uuid.uuid4(), duplicate_tenant, duplicate_key)],
        )
        failed_duplicate = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert "duplicate non-null (tenant_id, client_event_id)" in (
            f"{failed_duplicate.stdout}\n{failed_duplicate.stderr}"
        )
        await conn.execute(
            "DELETE FROM intent_events WHERE tenant_id=$1 AND client_event_id=$2",
            duplicate_tenant,
            duplicate_key,
        )

        await conn.execute("CREATE SCHEMA intent_decoy")
        await conn.execute(
            "CREATE TABLE intent_decoy.intent_events (tenant_id uuid NOT NULL, client_event_id varchar(100))"
        )
        shadow_tenant = uuid.uuid4()
        await conn.executemany(
            "INSERT INTO intent_decoy.intent_events (tenant_id, client_event_id) VALUES ($1, $2)",
            [(shadow_tenant, "shadow-duplicate"), (shadow_tenant, "shadow-duplicate")],
        )
        await conn.execute(f"CREATE INDEX {INDEX_NAME} ON intent_decoy.intent_events (tenant_id, client_event_id)")
        await conn.execute(
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_database} SET search_path TO intent_decoy, public"
        )
    finally:
        await conn.close()

    probe = await asyncpg.connect(dsn)
    try:
        assert await probe.fetchval("SHOW search_path") == "intent_decoy, public"
        assert await probe.fetchval("SELECT to_regclass('intent_events') = 'intent_decoy.intent_events'::regclass")
        await probe.execute(
            f"CREATE UNIQUE INDEX {INDEX_NAME} ON public.intent_events "
            "(client_event_id, tenant_id) WHERE client_event_id IS NOT NULL"
        )
    finally:
        await probe.close()

    wrong = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    assert f"Refusing to reuse valid index {INDEX_NAME}" in f"{wrong.stdout}\n{wrong.stderr}"
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=$1::regclass",
                f"public.{INDEX_NAME}",
            )
            is True
        )
        await conn.execute(f"DROP INDEX CONCURRENTLY public.{INDEX_NAME}")
        await conn.execute(
            f"CREATE UNIQUE INDEX {INDEX_NAME} ON public.intent_events "
            "(tenant_id, client_event_id) WHERE client_event_id IS NOT NULL"
        )
        await conn.execute(
            "UPDATE pg_index SET indisvalid=false WHERE indexrelid=$1::regclass",
            f"public.{INDEX_NAME}",
        )
    finally:
        await conn.close()

    locker = await asyncpg.connect(dsn)
    try:
        await locker.execute("BEGIN")
        await locker.execute("LOCK TABLE public.intent_events IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        lock_failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert time.monotonic() - started < 15
        assert "lock timeout" in f"{lock_failed.stdout}\n{lock_failed.stderr}"
        assert (
            await locker.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid='public.intent_events'::regclass")
            is False
        )
    finally:
        await locker.execute("ROLLBACK")
        await locker.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        after = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='public.intent_events'::regclass"
        )
        assert (after["relrowsecurity"], after["relforcerowsecurity"]) == (True, True)
        shadow = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='intent_decoy.intent_events'::regclass"
        )
        assert (shadow["relrowsecurity"], shadow["relforcerowsecurity"]) == (False, False)
        assert await conn.fetchval("SELECT count(*) FROM intent_decoy.intent_events") == 2
        exact = await conn.fetchrow(
            "SELECT i.indisvalid, i.indisready, i.indisunique, i.indrelid='public.intent_events'::regclass AS target, "
            "pg_get_expr(i.indpred, i.indrelid, true) AS predicate "
            "FROM pg_index i WHERE i.indexrelid=$1::regclass",
            f"public.{INDEX_NAME}",
        )
        assert dict(exact) == {
            "indisvalid": True,
            "indisready": True,
            "indisunique": True,
            "target": True,
            "predicate": "client_event_id IS NOT NULL",
        }
        assert await conn.fetchval("SHOW lock_timeout") == "0"
    finally:
        await conn.close()

    locker = await asyncpg.connect(dsn)
    try:
        await locker.execute("BEGIN")
        await locker.execute("LOCK TABLE public.intent_events IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        lock_failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert time.monotonic() - started < 15
        assert "lock timeout" in f"{lock_failed.stdout}\n{lock_failed.stderr}"
        assert (
            await locker.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid='public.intent_events'::regclass")
            is True
        )
    finally:
        await locker.execute("ROLLBACK")
        await locker.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        downgraded = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='public.intent_events'::regclass"
        )
        assert (downgraded["relrowsecurity"], downgraded["relforcerowsecurity"]) == (False, False)
        assert await conn.fetchval("SELECT to_regclass($1)", f"public.{INDEX_NAME}") is None
        assert await conn.fetchval("SELECT to_regclass($1)", f"intent_decoy.{INDEX_NAME}") is not None
        shadow = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='intent_decoy.intent_events'::regclass"
        )
        assert (shadow["relrowsecurity"], shadow["relforcerowsecurity"]) == (False, False)
    finally:
        await conn.execute(f"ALTER ROLE {quoted_role} IN DATABASE {quoted_database} RESET search_path")
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")
