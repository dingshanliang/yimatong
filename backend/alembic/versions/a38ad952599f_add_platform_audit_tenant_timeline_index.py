"""Add the online platform audit tenant timeline index.

Revision ID: a38ad952599f
Revises: 02c17094ef47
Create Date: 2026-08-09 16:42:27.798690

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a38ad952599f"
down_revision: str | None = "02c17094ef47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "platform_audit_log"
_INDEX = "ix_platform_audit_target_timeline"
_COLUMNS = ("target_tenant_id", "timestamp", "id")
_OPTIONS = (0, 3, 3)  # ASC NULLS LAST, then DESC NULLS FIRST twice.


def _resolve_table():
    table = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT tbl.oid AS table_oid, ns.oid AS schema_oid,
                       ns.nspname AS schema_name, tbl.relname AS table_name
                FROM pg_class AS tbl
                JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
                WHERE ns.nspname = 'public'
                  AND tbl.relname = :table_name
                  AND tbl.relkind IN ('r', 'p')
                """
            ),
            {"table_name": _TABLE},
        )
        .mappings()
        .one_or_none()
    )
    if table is None:
        raise RuntimeError(f"Required table public.{_TABLE} does not exist")
    return table


def _read_index(table):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT i.indrelid AS table_oid, i.indisvalid, i.indisready,
                       i.indislive, i.indisunique, am.amname AS access_method,
                       i.indpred IS NULL AS has_no_predicate,
                       i.indexprs IS NULL AS has_no_expressions,
                       i.indnatts = i.indnkeyatts AS has_no_includes,
                       NOT EXISTS (
                           SELECT 1
                           FROM unnest(i.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                           JOIN pg_opclass AS opc ON opc.oid = cls.opclass_oid
                           WHERE cls.ordinality <= i.indnkeyatts AND NOT opc.opcdefault
                       ) AS uses_default_opclasses,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = i.indrelid AND attr.attnum = key.attnum
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
                WHERE ns.oid = :schema_oid AND idx.relname = :index_name
                """
            ),
            {"schema_oid": table["schema_oid"], "index_name": _INDEX},
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
        and not row["indisunique"]
        and row["access_method"] == "btree"
        and row["has_no_predicate"]
        and row["has_no_expressions"]
        and row["has_no_includes"]
        and row["uses_default_opclasses"]
        and tuple(row["key_columns"]) == _COLUMNS
        and tuple(row["key_options"]) == _OPTIONS
    )


def _qualified_names(table) -> tuple[str, str, str, str]:
    preparer = op.get_bind().dialect.identifier_preparer
    schema = preparer.quote_identifier(table["schema_name"])
    table_name = preparer.quote_identifier(table["table_name"])
    index = preparer.quote_identifier(_INDEX)
    return schema, table_name, index, f"{schema}.{index}"


def _prepare_index() -> None:
    table = _resolve_table()
    row = _read_index(table)
    schema, table_name, index, qualified_index = _qualified_names(table)

    if row is not None and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        if row["table_oid"] != table["table_oid"]:
            raise RuntimeError(f"Refusing to drop invalid index {_INDEX}: it belongs to a different table")
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        row = None

    if row is not None:
        if not _is_exact_index(row, table):
            raise RuntimeError(
                f"Refusing to reuse valid index {_INDEX}: its table, keys, sort order, or options differ"
            )
        return

    op.execute(
        f"CREATE INDEX CONCURRENTLY {index} ON {schema}.{table_name} "
        '("target_tenant_id", "timestamp" DESC, "id" DESC)'
    )


def _drop_index() -> None:
    table = _resolve_table()
    row = _read_index(table)
    if row is None:
        return
    *_, qualified_index = _qualified_names(table)
    if row["table_oid"] == table["table_oid"] and not (
        row["indisvalid"] and row["indisready"] and row["indislive"]
    ):
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        return
    if not _is_exact_index(row, table):
        raise RuntimeError(f"Refusing to drop unexpected index {_INDEX} during downgrade")
    op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Alembic commits the preceding marker migration before entering this
    # block. Every statement here is autocommitted because PostgreSQL forbids
    # CREATE/DROP INDEX CONCURRENTLY inside an explicit transaction.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _prepare_index()
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _drop_index()
        finally:
            op.execute("RESET lock_timeout")
