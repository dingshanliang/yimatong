"""PostgreSQL proof for the active WeCom connector invariant."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import set_session_tenant_context
from app.services.wecom_integration import upsert_wecom_connector
from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "93f7b66a0ec6"
INDEX_NAME = "uq_connectors_active_wecom_tenant"


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


async def _insert_tenant(conn: asyncpg.Connection, tenant_id: uuid.UUID, marker: str) -> None:
    await conn.execute(
        "INSERT INTO tenants "
        "(id, name, slug, status, plan, plan_expires_at, tenant_type, created_at, updated_at) "
        "VALUES ($1, $2, $3, 'active', 'free', now() + interval '30 days', 'brand', now(), now())",
        tenant_id,
        f"WeCom invariant {marker}",
        f"wecom-invariant-{marker}-{tenant_id.hex[:8]}",
    )


async def test_concurrent_save_serializes_to_one_active_connector(migrated_pg_url: str):
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    tenant_id = uuid.uuid4()
    owner = await asyncpg.connect(owner_dsn)
    await _insert_tenant(owner, tenant_id, "concurrent")

    runtime_engine = create_async_engine(runtime_url)
    runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
    start = asyncio.Event()

    async def save(corp_id: str) -> dict:
        async with runtime_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            await start.wait()
            result = await upsert_wecom_connector(
                db,
                tenant_id,
                corp_id=corp_id,
                secret=f"secret-{corp_id}",
                customer_service_user_ids=[f"member-{corp_id}"],
                mock_mode=True,
            )
            await db.commit()
            return result

    first_task = asyncio.create_task(save("corp-a"))
    second_task = asyncio.create_task(save("corp-b"))
    await asyncio.sleep(0)
    start.set()
    try:
        first, second = await asyncio.gather(first_task, second_task)
        assert first["connector_id"] == second["connector_id"]
        rows = await owner.fetch(
            "SELECT id, config->>'corp_id' AS corp_id, enabled "
            "FROM connectors WHERE tenant_id=$1 AND connector_type='wecom_customer_contact'",
            tenant_id,
        )
        assert len(rows) == 1
        assert rows[0]["enabled"] is True
        assert rows[0]["corp_id"] in {"corp-a", "corp-b"}
    finally:
        await runtime_engine.dispose()
        deleted = await owner.fetchval(
            "DELETE FROM connectors WHERE tenant_id=$1 AND connector_type='wecom_customer_contact' RETURNING id",
            tenant_id,
        )
        assert deleted is not None
        assert await owner.fetchval("DELETE FROM tenants WHERE id=$1 RETURNING id", tenant_id) == tenant_id
        await owner.close()


async def test_migration_preflight_catalog_and_roundtrip(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id = uuid.uuid4()
    connector_a, connector_b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await _insert_tenant(conn, tenant_id, "migration")
        await conn.executemany(
            "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
            "VALUES ($1, $2, $3, 'wecom_customer_contact', '{}'::jsonb, true)",
            [
                (connector_a, tenant_id, "duplicate-a"),
                (connector_b, tenant_id, "duplicate-b"),
            ],
        )
    finally:
        await conn.close()

    try:
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        failure_output = f"{failed.stdout}\n{failed.stderr}"
        assert "active WeCom connector uniqueness preflight failed" in failure_output
        assert str(tenant_id) in failure_output

        conn = await asyncpg.connect(dsn)
        try:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM connectors "
                    "WHERE tenant_id=$1 AND connector_type='wecom_customer_contact' AND enabled IS TRUE",
                    tenant_id,
                )
                == 2
            )
            await conn.execute("UPDATE connectors SET enabled=false WHERE id=$1", connector_b)
        finally:
            await conn.close()

        _alembic(migrated_pg_url, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            catalog = await conn.fetchrow(
                "SELECT i.indisvalid, i.indisready, i.indislive, i.indisunique, "
                "pg_get_expr(i.indpred, i.indrelid) AS predicate, "
                "ARRAY(SELECT a.attname FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ord) "
                "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
                "WHERE k.ord <= i.indnkeyatts ORDER BY k.ord) AS keys "
                "FROM pg_index i WHERE i.indexrelid=$1::regclass",
                f"public.{INDEX_NAME}",
            )
            assert catalog is not None
            assert tuple(catalog["keys"]) == ("tenant_id",)
            assert (
                catalog["indisvalid"],
                catalog["indisready"],
                catalog["indislive"],
                catalog["indisunique"],
            ) == (True, True, True, True)
            assert "wecom_customer_contact" in catalog["predicate"]
            assert "enabled IS TRUE" in catalog["predicate"]
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                    "VALUES ($1, $2, 'blocked-duplicate', 'wecom_customer_contact', '{}'::jsonb, true)",
                    uuid.uuid4(),
                    tenant_id,
                )
        finally:
            await conn.close()

        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT to_regclass($1)::text", f"public.{INDEX_NAME}") is None
            await conn.execute(f"CREATE UNIQUE INDEX {INDEX_NAME} ON public.connectors (id)")
        finally:
            await conn.close()

        wrong_shape = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert f"Refusing to reuse valid index {INDEX_NAME}" in f"{wrong_shape.stdout}\n{wrong_shape.stderr}"
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=$1::regclass", f"public.{INDEX_NAME}"
            )
            await conn.execute(f"DROP INDEX CONCURRENTLY public.{INDEX_NAME}")
            await conn.execute(
                f"CREATE UNIQUE INDEX {INDEX_NAME} ON public.connectors (tenant_id) "
                "WHERE connector_type='wecom_customer_contact' AND enabled IS TRUE"
            )
            await conn.execute(
                "UPDATE pg_index SET indisvalid=false WHERE indexrelid=$1::regclass",
                f"public.{INDEX_NAME}",
            )
        finally:
            await conn.close()
        _alembic(migrated_pg_url, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT indisvalid AND indisready AND indislive FROM pg_index WHERE indexrelid=$1::regclass",
                f"public.{INDEX_NAME}",
            )
        finally:
            await conn.close()
        _alembic(
            migrated_pg_url,
            "-x",
            "baseline_legacy_timestamp_nullability=true",
            "check",
        )
    finally:
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP INDEX CONCURRENTLY IF EXISTS public." + INDEX_NAME)
            await conn.execute("DELETE FROM connectors WHERE tenant_id=$1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
        finally:
            await conn.close()
        _alembic(migrated_pg_url, "upgrade", "head")
