"""Enforce one active WeCom customer-contact connector per tenant.

Revision ID: d076027f6161
Revises: 93f7b66a0ec6
Create Date: 2026-08-09 19:24:48.980056

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d076027f6161"
down_revision: str | None = "93f7b66a0ec6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "connectors"
_INDEX = "uq_connectors_active_wecom_tenant"
_KEY_COLUMNS = ("tenant_id",)
_PREDICATE = "connector_type = 'wecom_customer_contact' AND enabled IS TRUE"


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
                       pg_get_expr(i.indpred, i.indrelid) AS predicate,
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


def _predicate_is_exact(predicate: str | None) -> bool:
    if predicate is None:
        return False
    normalized = " ".join(predicate.replace("::character varying", "::text").split())
    return normalized in {
        "(((connector_type)::text = 'wecom_customer_contact'::text) AND (enabled IS TRUE))",
        "((connector_type)::text = 'wecom_customer_contact'::text) AND (enabled IS TRUE)",
    }


def _is_exact_index(row, table) -> bool:
    return bool(
        row
        and row["table_oid"] == table["table_oid"]
        and row["indisvalid"]
        and row["indisready"]
        and row["indislive"]
        and row["indisunique"]
        and row["access_method"] == "btree"
        and row["has_no_expressions"]
        and row["has_no_includes"]
        and row["uses_default_opclasses"]
        and tuple(row["key_columns"]) == _KEY_COLUMNS
        and tuple(row["key_options"]) == (0,)
        and _predicate_is_exact(row["predicate"])
    )


def _qualified_names(table) -> tuple[str, str, str, str]:
    preparer = op.get_bind().dialect.identifier_preparer
    schema = preparer.quote_identifier(table["schema_name"])
    table_name = preparer.quote_identifier(table["table_name"])
    index = preparer.quote_identifier(_INDEX)
    return schema, table_name, index, f"{schema}.{index}"


def _preflight_duplicates() -> None:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT tenant_id::text AS tenant_id, count(*) AS active_count
                FROM public.connectors
                WHERE connector_type = 'wecom_customer_contact' AND enabled IS TRUE
                GROUP BY tenant_id
                HAVING count(*) > 1
                ORDER BY tenant_id
                LIMIT 20
                """
            )
        )
        .mappings()
        .all()
    )
    if rows:
        summary = ", ".join(f"{row['tenant_id']}={row['active_count']}" for row in rows)
        raise RuntimeError(
            "active WeCom connector uniqueness preflight failed: "
            f"duplicate tenants ({summary}); disable or consolidate duplicates before retrying"
        )


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
                f"Refusing to reuse valid index {_INDEX}: its table, key, predicate, uniqueness, or options differ"
            )
        return

    op.execute(
        f"CREATE UNIQUE INDEX CONCURRENTLY {index} ON {schema}.{table_name} (tenant_id) WHERE {_PREDICATE}"
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
        op.create_index(
            _INDEX,
            _TABLE,
            list(_KEY_COLUMNS),
            unique=True,
            sqlite_where=sa.text("connector_type = 'wecom_customer_contact' AND enabled = 1"),
        )
        return
    _preflight_duplicates()
    # PostgreSQL forbids concurrent index DDL inside a transaction. The
    # preflight transaction commits before this block; a duplicate introduced
    # during the build still makes CREATE UNIQUE INDEX fail closed and leaves
    # an invalid shell that the next retry safely removes.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _prepare_index()
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.drop_index(_INDEX, table_name=_TABLE)
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _drop_index()
        finally:
            op.execute("RESET lock_timeout")
