"""secure intent event telemetry

Revision ID: j618bf978c10
Revises: a313fa710459
Create Date: 2026-08-03 21:30:00.000000

Enable strict tenant RLS for intent_events and add the database authority for
client telemetry idempotency. Existing duplicate non-null client event IDs
fail the migration before index construction; operators must investigate them
rather than silently discard business evidence.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j618bf978c10"
down_revision: str | None = "a313fa710459"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE_INDEX = "uq_intent_events_tenant_client_event"
_RLS_POLICY = "intent_events_tenant_isolation"
_INDEX_COLUMNS = ("tenant_id", "client_event_id")
_INDEX_PREDICATE = "client_event_id IS NOT NULL"
_LOCK_TIMEOUT = "5s"
_TARGET_SCHEMA = "public"
_TARGET_TABLE = "intent_events"


def _resolve_table():
    qualified_table = f"{_TARGET_SCHEMA}.{_TARGET_TABLE}"
    table = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT tbl.oid AS table_oid, ns.oid AS schema_oid,
                       ns.nspname AS schema_name, tbl.relname AS table_name
                FROM pg_class AS tbl
                JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
                WHERE tbl.oid = to_regclass(:table_name)
                  AND tbl.relkind IN ('r', 'p')
                """
            ),
            {"table_name": qualified_table},
        )
        .mappings()
        .one_or_none()
    )
    if table is None:
        raise RuntimeError(f"Required table {qualified_table} does not exist")
    return table


def _read_target_schema_index(table, index_name: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    i.indrelid AS table_oid,
                    i.indisvalid,
                    i.indisready,
                    i.indislive,
                    i.indisunique,
                    am.amname AS access_method,
                    pg_get_expr(i.indpred, i.indrelid, true) AS predicate,
                    i.indexprs IS NULL AS has_no_expressions,
                    i.indnatts = i.indnkeyatts AS has_no_includes,
                    NOT EXISTS (
                        SELECT 1
                        FROM unnest(i.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                        JOIN pg_opclass AS opc ON opc.oid = cls.opclass_oid
                        WHERE cls.ordinality <= i.indnkeyatts
                          AND NOT opc.opcdefault
                    ) AS uses_default_opclasses,
                    ARRAY(
                        SELECT attr.attname
                        FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                        JOIN pg_attribute AS attr
                          ON attr.attrelid = i.indrelid
                         AND attr.attnum = key.attnum
                        WHERE key.ordinality <= i.indnkeyatts
                        ORDER BY key.ordinality
                    ) AS key_columns,
                    ARRAY(
                        SELECT option
                        FROM unnest(i.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                        WHERE opts.ordinality <= i.indnkeyatts
                        ORDER BY opts.ordinality
                    ) AS key_options
                FROM pg_class AS idx
                JOIN pg_namespace AS ns ON ns.oid = idx.relnamespace
                JOIN pg_index AS i ON i.indexrelid = idx.oid
                JOIN pg_am AS am ON am.oid = idx.relam
                WHERE ns.oid = :schema_oid
                  AND idx.relname = :index_name
                """
            ),
            {"schema_oid": table["schema_oid"], "index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )


def _is_exact_index(row, table) -> bool:
    return bool(
        row
        and row["table_oid"] == table["table_oid"]
        and row["indisvalid"]
        and row["indisready"]
        and row["indislive"]
        and row["indisunique"]
        and row["access_method"] == "btree"
        and row["predicate"] == _INDEX_PREDICATE
        and row["has_no_expressions"]
        and row["has_no_includes"]
        and row["uses_default_opclasses"]
        and tuple(row["key_columns"]) == _INDEX_COLUMNS
        and all(option == 0 for option in row["key_options"])
    )


def _qualified_names(table):
    preparer = op.get_bind().dialect.identifier_preparer
    quoted_schema = preparer.quote_identifier(table["schema_name"])
    quoted_table = preparer.quote_identifier(table["table_name"])
    quoted_index = preparer.quote_identifier(_UNIQUE_INDEX)
    return quoted_schema, quoted_table, quoted_index, f"{quoted_schema}.{quoted_index}"


def _prepare_unique_index() -> None:
    table = _resolve_table()
    row = _read_target_schema_index(table, _UNIQUE_INDEX)
    quoted_schema, quoted_table, quoted_index, qualified_index = _qualified_names(table)

    if row is not None and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        if row["table_oid"] != table["table_oid"]:
            raise RuntimeError(f"Refusing to drop invalid index {_UNIQUE_INDEX}: it belongs to a different table")
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        row = None

    if row is not None:
        if not _is_exact_index(row, table):
            raise RuntimeError(
                f"Refusing to reuse valid index {_UNIQUE_INDEX}: its table, uniqueness, "
                "access method, keys, predicate, expressions, includes, opclasses, or sort options differ"
            )
        return

    preparer = op.get_bind().dialect.identifier_preparer
    quoted_columns = ", ".join(preparer.quote_identifier(column) for column in _INDEX_COLUMNS)
    op.execute(
        f"CREATE UNIQUE INDEX CONCURRENTLY {quoted_index} "
        f"ON {quoted_schema}.{quoted_table} ({quoted_columns}) WHERE {_INDEX_PREDICATE}"
    )


def _drop_exact_unique_index() -> None:
    table = _resolve_table()
    row = _read_target_schema_index(table, _UNIQUE_INDEX)
    if row is None:
        return
    if row["table_oid"] == table["table_oid"] and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        *_, qualified_index = _qualified_names(table)
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        return
    if not _is_exact_index(row, table):
        raise RuntimeError(f"Refusing to drop unexpected index {_UNIQUE_INDEX} during downgrade")
    *_, qualified_index = _qualified_names(table)
    op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")


def upgrade() -> None:
    table = _resolve_table()
    quoted_schema, quoted_table, _, _ = _qualified_names(table)
    qualified_table = f"{quoted_schema}.{quoted_table}"
    preparer = op.get_bind().dialect.identifier_preparer
    tenant_column = preparer.quote_identifier("tenant_id")
    client_event_column = preparer.quote_identifier("client_event_id")
    quoted_policy = preparer.quote_identifier(_RLS_POLICY)
    duplicate_group_count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM (
                SELECT {tenant_column}, {client_event_column}
                FROM {qualified_table}
                WHERE {client_event_column} IS NOT NULL
                GROUP BY {tenant_column}, {client_event_column}
                HAVING count(*) > 1
            ) AS duplicate_groups
            """
        )
    )
    if duplicate_group_count:
        raise RuntimeError(
            "intent_events contains duplicate non-null (tenant_id, client_event_id) groups; "
            "investigate and resolve them before retrying this migration"
        )

    # A UNIQUE constraint cannot be added NOT VALID. Build the partial unique
    # index without blocking normal reads/writes. Dropping first also makes a
    # retry recover from a failed concurrent build that left an invalid index.
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        try:
            _prepare_unique_index()
        finally:
            op.execute("SET lock_timeout = DEFAULT")

    # These table-locking statements are deliberately bounded. A timeout is a
    # safe retry signal for operators to rerun during a quieter write window.
    # SET LOCAL keeps the bound through the remaining transactional RLS DDL.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"ALTER TABLE {qualified_table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified_table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {quoted_policy} ON {qualified_table}")
    op.execute(
        f"""
        CREATE POLICY {quoted_policy} ON {qualified_table}
        USING (
            {tenant_column} = current_tenant_id()
            OR (
                current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        WITH CHECK (
            {tenant_column} = current_tenant_id()
            OR (
                current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        try:
            _drop_exact_unique_index()
        finally:
            op.execute("SET lock_timeout = DEFAULT")

    table = _resolve_table()
    quoted_schema, quoted_table, _, _ = _qualified_names(table)
    qualified_table = f"{quoted_schema}.{quoted_table}"
    preparer = op.get_bind().dialect.identifier_preparer
    quoted_policy = preparer.quote_identifier(_RLS_POLICY)
    # As in upgrade, lock contention is retryable and should be handled in a
    # quieter deployment window instead of waiting without an upper bound.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"DROP POLICY IF EXISTS {quoted_policy} ON {qualified_table}")
    op.execute(f"ALTER TABLE {qualified_table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified_table} DISABLE ROW LEVEL SECURITY")
