"""PostgreSQL proof for connector/benefit tenant-consistency migration."""

import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "937c5e56d157"


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
    if succeeds:
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    else:
        assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result


async def _insert_tenant(conn: asyncpg.Connection, tenant_id: uuid.UUID, suffix: str) -> None:
    await conn.execute(
        "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
        "VALUES ($1, $2, $3, 'active', 'free', 'brand', now(), now())",
        tenant_id,
        f"connector consistency {suffix}",
        f"connector-consistency-{suffix}-{tenant_id.hex[:8]}",
    )


async def _insert_benefit(
    conn: asyncpg.Connection,
    benefit_id: uuid.UUID,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    name: str,
) -> None:
    await conn.execute(
        "INSERT INTO benefits "
        "(id, tenant_id, name, benefit_type, config_json, connector_id, stock_total, stock_used, "
        "per_person_limit, status) "
        "VALUES ($1, $2, $3, 'platform_coupon', '{}'::jsonb, $4, 1, 0, 1, 'active')",
        benefit_id,
        tenant_id,
        name,
        connector_id,
    )


async def _insert_connector(
    conn: asyncpg.Connection,
    connector_id: uuid.UUID,
    tenant_id: uuid.UUID,
    name: str,
) -> None:
    await conn.execute(
        "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
        "VALUES ($1, $2, $3, 'generic_http', '{}'::jsonb, true)",
        connector_id,
        tenant_id,
        name,
    )


async def _insert_delivery(
    conn: asyncpg.Connection,
    delivery_id: uuid.UUID,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    benefit_id: uuid.UUID,
    consumer_id: str,
) -> None:
    await conn.execute(
        "INSERT INTO benefit_deliveries "
        "(id, tenant_id, connector_id, benefit_id, consumer_id, benefit_type, benefit_config, status, "
        "retry_count, max_retries) "
        "VALUES ($1, $2, $3, $4, $5, 'platform_coupon', '{}'::jsonb, 'pending', 0, 5)",
        delivery_id,
        tenant_id,
        connector_id,
        benefit_id,
        consumer_id,
    )


async def test_dirty_cross_tenant_references_block_upgrade_and_constraints_are_reversible(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    connector_b, benefit_a, benefit_b, delivery_a = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(dsn)
    try:
        await _insert_tenant(conn, tenant_a, "a")
        await _insert_tenant(conn, tenant_b, "b")
        await _insert_connector(conn, connector_b, tenant_b, "tenant b connector")
        await _insert_benefit(conn, benefit_a, tenant_a, connector_b, "cross tenant benefit")
        await _insert_benefit(conn, benefit_b, tenant_b, connector_b, "tenant b benefit")
        await _insert_delivery(conn, delivery_a, tenant_a, connector_b, benefit_b, "consumer-cross")
    finally:
        await conn.close()

    failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    failure_output = f"{failed.stdout}\n{failed.stderr}"
    assert "connector tenant consistency preflight failed" in failure_output
    assert "benefits=1, delivery_connectors=1, delivery_benefits=1" in failure_output

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DELETE FROM benefit_deliveries WHERE id = $1", delivery_a)
        await conn.execute("DELETE FROM benefits WHERE id = $1", benefit_a)
    finally:
        await conn.close()

    # Put an empty schema first so current_schema() differs from the schema
    # containing the real tables. New migration connections inherit this path.
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE SCHEMA empty_first")
        database_name = await conn.fetchval("SELECT current_database()")
        quoted_database = await conn.fetchval("SELECT quote_ident($1)", database_name)
        current_role = await conn.fetchval("SELECT current_user")
        quoted_role = await conn.fetchval("SELECT quote_ident($1)", current_role)
        await conn.execute(
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_database} SET search_path TO empty_first, public"
        )
    finally:
        await conn.close()
    search_path_probe = await asyncpg.connect(dsn)
    try:
        assert await search_path_probe.fetchval("SHOW search_path") == "empty_first, public"
        assert await search_path_probe.fetchval("SELECT to_regclass('connectors')::text") == "connectors"
    finally:
        await search_path_probe.close()

    # A same-name valid index with the wrong key order must fail closed and
    # remain untouched for operator investigation.
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE UNIQUE INDEX uq_connectors_tenant_id_id ON connectors (id, tenant_id)")
    finally:
        await conn.close()
    wrong_index = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    assert "Refusing to reuse valid index uq_connectors_tenant_id_id" in (f"{wrong_index.stdout}\n{wrong_index.stderr}")
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_connectors_tenant_id_id'"
            )
            is True
        )
        await conn.execute("DROP INDEX CONCURRENTLY uq_connectors_tenant_id_id")

        # Deterministically simulate a cancelled concurrent build by marking a
        # correctly defined index invalid. Upgrade must drop/rebuild this shell.
        await conn.execute("CREATE UNIQUE INDEX uq_connectors_tenant_id_id ON connectors (tenant_id, id)")
        await conn.execute("CREATE UNIQUE INDEX uq_benefits_tenant_id_id ON benefits (tenant_id, id)")
        await conn.execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'uq_connectors_tenant_id_id'::regclass"
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    constrained_benefit = uuid.uuid4()
    rls_role = f"benefit_delivery_rls_{uuid.uuid4().hex[:12]}"
    try:
        await conn.execute(f"CREATE ROLE {rls_role} NOLOGIN")
        assert (
            await conn.fetchval(
                "SELECT bool_and(convalidated) FROM pg_constraint WHERE conname = ANY($1::text[])",
                [
                    "fk_benefits_tenant_connector",
                    "fk_benefit_deliveries_tenant_connector",
                    "fk_benefit_deliveries_tenant_benefit",
                ],
            )
            is True
        )
        index_rows = await conn.fetch(
            "SELECT c.relname, i.indisvalid, i.indisunique "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = ANY($1::text[]) ORDER BY c.relname",
            ["uq_benefits_tenant_id_id", "uq_connectors_tenant_id_id"],
        )
        assert [(row["relname"], row["indisvalid"], row["indisunique"]) for row in index_rows] == [
            ("uq_benefits_tenant_id_id", True, True),
            ("uq_connectors_tenant_id_id", True, True),
        ]
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _insert_benefit(conn, constrained_benefit, tenant_a, connector_b, "constraint probe")

        # The single-column ON DELETE action plus composite tenant FK preserve
        # the non-null tenant scope while detaching the connector.
        connector_delete, benefit_delete = uuid.uuid4(), uuid.uuid4()
        await conn.execute("SELECT set_config('app.bypass_rls', 'true', false)")
        await _insert_connector(conn, connector_delete, tenant_a, "delete semantics connector")
        await _insert_benefit(conn, benefit_delete, tenant_a, connector_delete, "delete semantics benefit")
        await conn.execute("DELETE FROM connectors WHERE id = $1", connector_delete)
        deleted_row = await conn.fetchrow(
            "SELECT tenant_id, connector_id FROM benefits WHERE id = $1",
            benefit_delete,
        )
        assert deleted_row["tenant_id"] == tenant_a
        assert deleted_row["connector_id"] is None

        # RLS matrix: tenant context sees only itself, missing context sees
        # nothing, and only the explicit bypass flag unlocks cross-tenant work.
        connector_a, benefit_for_a, delivery_for_a, delivery_for_b = (uuid.uuid4() for _ in range(4))
        await _insert_connector(conn, connector_a, tenant_a, "tenant a connector")
        await _insert_benefit(conn, benefit_for_a, tenant_a, connector_a, "tenant a benefit")
        await _insert_delivery(conn, delivery_for_a, tenant_a, connector_a, benefit_for_a, "consumer-a")
        await _insert_delivery(conn, delivery_for_b, tenant_b, connector_b, benefit_b, "consumer-b")

        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {rls_role}")
        await conn.execute(f"GRANT SELECT, INSERT ON benefit_deliveries TO {rls_role}")
        await conn.execute(f"SET ROLE {rls_role}")
        await conn.execute("SELECT set_config('app.bypass_rls', 'false', false)")
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        assert await conn.fetchval("SELECT count(*) FROM benefit_deliveries") == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _insert_delivery(conn, uuid.uuid4(), tenant_b, connector_b, benefit_b, "cross-write")

        await conn.execute("RESET app.tenant_id")
        assert await conn.fetchval("SELECT count(*) FROM benefit_deliveries") == 0
        await conn.execute("SELECT set_config('app.bypass_rls', 'true', false)")
        assert await conn.fetchval("SELECT count(*) FROM benefit_deliveries") == 2
    finally:
        await conn.execute("RESET ROLE")
        await conn.execute(f"DROP OWNED BY {rls_role}")
        await conn.execute(f"DROP ROLE IF EXISTS {rls_role}")
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        await _insert_benefit(conn, constrained_benefit, tenant_a, connector_b, "downgrade probe")
        await conn.execute("DELETE FROM benefits WHERE id = $1", constrained_benefit)
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")
