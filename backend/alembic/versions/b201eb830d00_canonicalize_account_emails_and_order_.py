"""Canonicalize account emails and order WeCom relationship events.

Revision ID: b201eb830d00
Revises: j618bf978c10
Create Date: 2026-08-03 22:34:00.144143

Account normalization and WeCom backfill run in bounded, retryable batches.
Each batch commits independently so locks are released between batches; a
timeout can safely be retried because both transformations are idempotent.

Downgrade is intentionally lossy: it cannot recover original email casing or
whitespace, and dropping ``event_time``/``event_sequence`` removes the durable
ordering proof for already-processed WeCom callbacks. It preserves the current
relationship status and normalized account identities.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b201eb830d00"
down_revision: str | None = "j618bf978c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "public"
_ACCOUNTS = "accounts"
_WECOM = "wecom_external_contacts"
_CANONICAL_CHECK = "ck_accounts_email_canonical"
_CASE_INSENSITIVE_INDEX = "uq_accounts_tenant_email_ci"
_LEGACY_UNIQUE = "uq_account_tenant_email"
_BATCH_SIZE = 5000
_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "30s"


def _resolve_table(table_name: str):
    qualified = f"{_SCHEMA}.{table_name}"
    table = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT tbl.oid AS table_oid, ns.oid AS schema_oid,
                       ns.nspname AS schema_name, tbl.relname AS table_name
                FROM pg_class AS tbl
                JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
                WHERE tbl.oid = to_regclass(:qualified)
                  AND ns.nspname = :schema_name
                  AND tbl.relname = :table_name
                  AND tbl.relkind = 'r'
                """
            ),
            {"qualified": qualified, "schema_name": _SCHEMA, "table_name": table_name},
        )
        .mappings()
        .one_or_none()
    )
    if table is None:
        raise RuntimeError(f"Required table {qualified} does not exist")
    return table


def _quoted_table(table) -> str:
    preparer = op.get_bind().dialect.identifier_preparer
    return f"{preparer.quote_identifier(table['schema_name'])}.{preparer.quote_identifier(table['table_name'])}"


def _qualified_index(table, index_name: str) -> str:
    preparer = op.get_bind().dialect.identifier_preparer
    return f"{preparer.quote_identifier(table['schema_name'])}.{preparer.quote_identifier(index_name)}"


def _read_index(table, index_name: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT i.indrelid AS table_oid,
                       i.indisvalid, i.indisready, i.indislive, i.indisunique,
                       i.indnatts, i.indnkeyatts,
                       am.amname AS access_method,
                       pg_get_expr(i.indpred, i.indrelid, true) AS predicate,
                       pg_get_expr(i.indexprs, i.indrelid, true) AS expression,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           LEFT JOIN pg_attribute AS attr
                             ON attr.attrelid = i.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= i.indnkeyatts
                           ORDER BY key.ordinality
                       ) AS key_columns,
                       ARRAY(
                           SELECT option
                           FROM unnest(i.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                           WHERE opts.ordinality <= i.indnkeyatts
                           ORDER BY opts.ordinality
                       ) AS key_options,
                       NOT EXISTS (
                           SELECT 1
                           FROM unnest(i.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                           JOIN pg_opclass AS opc ON opc.oid = cls.opclass_oid
                           WHERE cls.ordinality <= i.indnkeyatts AND NOT opc.opcdefault
                       ) AS uses_default_opclasses
                FROM pg_class AS idx
                JOIN pg_namespace AS ns ON ns.oid = idx.relnamespace
                JOIN pg_index AS i ON i.indexrelid = idx.oid
                JOIN pg_am AS am ON am.oid = idx.relam
                WHERE ns.oid = :schema_oid AND idx.relname = :index_name
                """
            ),
            {"schema_oid": table["schema_oid"], "index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )


def _is_exact_email_index(row, table, *, require_valid: bool) -> bool:
    valid = row and (not require_valid or (row["indisvalid"] and row["indisready"] and row["indislive"]))
    return bool(
        valid
        and row["table_oid"] == table["table_oid"]
        and row["indisunique"]
        and row["indnatts"] == 2
        and row["indnkeyatts"] == 2
        and row["access_method"] == "btree"
        and row["predicate"] is None
        and tuple(row["key_columns"]) == ("tenant_id", None)
        and row["expression"] in {"lower(email::text)", "lower((email)::text)"}
        and all(option == 0 for option in row["key_options"])
        and row["uses_default_opclasses"]
    )


def _assert_email_index_slot(table) -> None:
    row = _read_index(table, _CASE_INSENSITIVE_INDEX)
    if row is not None and not _is_exact_email_index(row, table, require_valid=False):
        raise RuntimeError(
            f"Refusing to reuse index {_CASE_INSENSITIVE_INDEX}: its schema, table OID, uniqueness, "
            "method, keys, expression, predicate, opclasses, or sort options differ"
        )


def _prepare_email_index(table) -> None:
    row = _read_index(table, _CASE_INSENSITIVE_INDEX)
    qualified_index = _qualified_index(table, _CASE_INSENSITIVE_INDEX)
    if row is not None and not _is_exact_email_index(row, table, require_valid=False):
        raise RuntimeError(f"Refusing to replace unexpected index {_CASE_INSENSITIVE_INDEX}")
    if row is not None and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        row = None
    if row is not None:
        if not _is_exact_email_index(row, table, require_valid=True):  # pragma: no cover - defensive
            raise RuntimeError(f"Refusing to reuse invalid index {_CASE_INSENSITIVE_INDEX}")
        return
    op.execute(
        f"CREATE UNIQUE INDEX CONCURRENTLY {_CASE_INSENSITIVE_INDEX} "
        f"ON {_quoted_table(table)} (tenant_id, lower(email))"
    )


def _drop_exact_email_index(table) -> None:
    row = _read_index(table, _CASE_INSENSITIVE_INDEX)
    if row is None:
        return
    if not _is_exact_email_index(row, table, require_valid=False):
        raise RuntimeError(f"Refusing to drop unexpected index {_CASE_INSENSITIVE_INDEX}")
    op.execute(f"DROP INDEX CONCURRENTLY {_qualified_index(table, _CASE_INSENSITIVE_INDEX)}")


def _read_constraint(table, constraint_name: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT con.oid, con.conrelid AS table_oid, con.contype, con.convalidated,
                       pg_get_constraintdef(con.oid) AS definition,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(con.conkey) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = con.conrelid AND attr.attnum = key.attnum
                           ORDER BY key.ordinality
                       ) AS key_columns
                FROM pg_constraint AS con
                JOIN pg_namespace AS ns ON ns.oid = con.connamespace
                WHERE ns.oid = :schema_oid AND con.conname = :constraint_name
                """
            ),
            {"schema_oid": table["schema_oid"], "constraint_name": constraint_name},
        )
        .mappings()
        .one_or_none()
    )


def _constraint_type(row) -> str:
    value = row["contype"]
    return value.decode() if isinstance(value, bytes) else value


def _ensure_canonical_check(table) -> None:
    row = _read_constraint(table, _CANONICAL_CHECK)
    expected = "CHECK (((email)::text = lower(TRIM(BOTH FROM email))))"
    if row is not None and not (
        row["table_oid"] == table["table_oid"] and _constraint_type(row) == "c" and row["definition"] == expected
    ):
        raise RuntimeError(f"Refusing to reuse unexpected constraint {_CANONICAL_CHECK}")
    qualified_table = _quoted_table(table)
    if row is None:
        op.execute(
            f"ALTER TABLE {qualified_table} ADD CONSTRAINT {_CANONICAL_CHECK} "
            "CHECK (email = lower(trim(email))) NOT VALID"
        )
    op.execute(f"ALTER TABLE {qualified_table} VALIDATE CONSTRAINT {_CANONICAL_CHECK}")


def _drop_legacy_unique_last(table) -> None:
    row = _read_constraint(table, _LEGACY_UNIQUE)
    if row is None:
        return
    if not (
        row["table_oid"] == table["table_oid"]
        and _constraint_type(row) == "u"
        and tuple(row["key_columns"]) == ("tenant_id", "email")
    ):
        raise RuntimeError(f"Refusing to drop unexpected constraint {_LEGACY_UNIQUE}")
    op.execute(f"ALTER TABLE {_quoted_table(table)} DROP CONSTRAINT {_LEGACY_UNIQUE}")


def _legacy_constraint_is_exact(row, table) -> bool:
    return bool(
        row
        and row["table_oid"] == table["table_oid"]
        and _constraint_type(row) == "u"
        and tuple(row["key_columns"]) == ("tenant_id", "email")
    )


def _is_exact_legacy_index(row, table, *, require_valid: bool) -> bool:
    valid = row and (not require_valid or (row["indisvalid"] and row["indisready"] and row["indislive"]))
    return bool(
        valid
        and row["table_oid"] == table["table_oid"]
        and row["indisunique"]
        and row["indnatts"] == 2
        and row["indnkeyatts"] == 2
        and row["access_method"] == "btree"
        and row["predicate"] is None
        and row["expression"] is None
        and tuple(row["key_columns"]) == ("tenant_id", "email")
        and all(option == 0 for option in row["key_options"])
        and row["uses_default_opclasses"]
    )


def _prepare_legacy_unique_index(table) -> None:
    row = _read_index(table, _LEGACY_UNIQUE)
    qualified_index = _qualified_index(table, _LEGACY_UNIQUE)
    if row is not None and not _is_exact_legacy_index(row, table, require_valid=False):
        raise RuntimeError(f"Refusing to replace unexpected index {_LEGACY_UNIQUE}")
    if row is not None and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        op.execute(f"DROP INDEX CONCURRENTLY {qualified_index}")
        row = None
    if row is not None:
        if not _is_exact_legacy_index(row, table, require_valid=True):  # pragma: no cover
            raise RuntimeError(f"Refusing to reuse invalid index {_LEGACY_UNIQUE}")
        return
    op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY {_LEGACY_UNIQUE} ON {_quoted_table(table)} (tenant_id, email)")


def _run_retryable_batches(statement: sa.TextClause) -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")
        try:
            while True:
                result = op.get_bind().execute(statement, {"batch_size": _BATCH_SIZE})
                if result.rowcount == 0:
                    break
        finally:
            op.execute("SET statement_timeout = DEFAULT")
            op.execute("SET lock_timeout = DEFAULT")


def _set_not_null_without_table_scan(table, column_name: str, check_name: str) -> None:
    qualified_table = _quoted_table(table)
    # The temporary validated CHECK lets PostgreSQL prove SET NOT NULL without
    # another full-table scan while the ACCESS EXCLUSIVE lock is held.
    op.execute(f"ALTER TABLE {qualified_table} DROP CONSTRAINT IF EXISTS {check_name}")
    op.execute(f"ALTER TABLE {qualified_table} ADD CONSTRAINT {check_name} CHECK ({column_name} IS NOT NULL) NOT VALID")
    op.execute(f"ALTER TABLE {qualified_table} VALIDATE CONSTRAINT {check_name}")
    op.execute(f"ALTER TABLE {qualified_table} ALTER COLUMN {column_name} SET NOT NULL")
    op.execute(f"ALTER TABLE {qualified_table} DROP CONSTRAINT {check_name}")


def upgrade() -> None:
    accounts = _resolve_table(_ACCOUNTS)
    wecom = _resolve_table(_WECOM)
    accounts_table = _quoted_table(accounts)
    wecom_table = _quoted_table(wecom)

    _assert_email_index_slot(accounts)
    duplicate_groups = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*) FROM (
                SELECT tenant_id, lower(trim(email)) AS canonical_email
                FROM {accounts_table}
                GROUP BY tenant_id, lower(trim(email))
                HAVING count(*) > 1
            ) AS collisions
            """
        )
    )
    if duplicate_groups:
        raise RuntimeError(
            "public.accounts contains tenant-scoped email identities that collide after lower(trim(email)); "
            "resolve the conflicting accounts explicitly before retrying this migration"
        )

    _run_retryable_batches(
        sa.text(
            f"""
            WITH batch AS (
                SELECT id FROM {accounts_table}
                WHERE email <> lower(trim(email))
                ORDER BY id LIMIT :batch_size FOR UPDATE SKIP LOCKED
            )
            UPDATE {accounts_table} AS accounts
            SET email = lower(trim(accounts.email))
            FROM batch WHERE accounts.id = batch.id
            """
        )
    )
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")
        try:
            _prepare_email_index(accounts)
        finally:
            op.execute("SET statement_timeout = DEFAULT")
            op.execute("SET lock_timeout = DEFAULT")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    _ensure_canonical_check(accounts)
    _drop_legacy_unique_last(accounts)

    # ADD IF NOT EXISTS makes a retry safe if a later batch timed out after the
    # migration's earlier autocommit blocks had already committed this DDL.
    op.execute(f"ALTER TABLE {wecom_table} ADD COLUMN IF NOT EXISTS event_time TIMESTAMPTZ")
    op.execute(f"ALTER TABLE {wecom_table} ADD COLUMN IF NOT EXISTS event_sequence BIGINT")
    _run_retryable_batches(
        sa.text(
            f"""
            WITH batch AS (
                SELECT id FROM {wecom_table}
                WHERE event_time IS NULL OR event_sequence IS NULL
                ORDER BY id LIMIT :batch_size FOR UPDATE SKIP LOCKED
            )
            UPDATE {wecom_table} AS contacts
            SET event_time = COALESCE(
                    contacts.event_time,
                    CASE
                        WHEN COALESCE(contacts.raw_event->>'CreateTime', contacts.raw_event->>'create_time', '')
                             ~ '^[0-9]+$'
                        THEN CASE
                            WHEN (COALESCE(
                                    contacts.raw_event->>'CreateTime', contacts.raw_event->>'create_time'
                                 ))::numeric BETWEEN 0 AND 253402300799
                            THEN to_timestamp(
                                (COALESCE(
                                    contacts.raw_event->>'CreateTime', contacts.raw_event->>'create_time'
                                ))::double precision
                            )
                            WHEN contacts.status = 'deleted'
                            THEN COALESCE(contacts.deleted_at, contacts.added_at, contacts.created_at, now())
                            ELSE COALESCE(contacts.added_at, contacts.deleted_at, contacts.created_at, now())
                        END
                        WHEN contacts.status = 'deleted' THEN
                            COALESCE(contacts.deleted_at, contacts.added_at, contacts.created_at, now())
                        ELSE
                            COALESCE(contacts.added_at, contacts.deleted_at, contacts.created_at, now())
                    END
                ),
                event_sequence = COALESCE(
                    contacts.event_sequence,
                    CASE
                        WHEN COALESCE(contacts.raw_event->>'Sequence', contacts.raw_event->>'sequence', '') ~ '^[0-9]+$'
                        THEN CASE
                            WHEN (COALESCE(
                                    contacts.raw_event->>'Sequence', contacts.raw_event->>'sequence'
                                 ))::numeric BETWEEN 0 AND 9223372036854775807
                            THEN (COALESCE(
                                contacts.raw_event->>'Sequence', contacts.raw_event->>'sequence'
                            ))::bigint
                            ELSE 0
                        END
                        ELSE 0
                    END
                )
            FROM batch WHERE contacts.id = batch.id
            """
        )
    )
    # _run_retryable_batches ended an autocommit block. Re-establish bounded
    # timeouts for every remaining DDL statement in the new transaction.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    op.execute(f"ALTER TABLE {wecom_table} ALTER COLUMN event_time SET DEFAULT now()")
    op.execute(f"ALTER TABLE {wecom_table} ALTER COLUMN event_sequence SET DEFAULT 0")
    _set_not_null_without_table_scan(wecom, "event_time", "ck_wecom_external_contacts_event_time_nn")
    _set_not_null_without_table_scan(wecom, "event_sequence", "ck_wecom_external_contacts_event_sequence_nn")


def downgrade() -> None:
    accounts = _resolve_table(_ACCOUNTS)
    wecom = _resolve_table(_WECOM)
    accounts_table = _quoted_table(accounts)
    wecom_table = _quoted_table(wecom)

    legacy_constraint = _read_constraint(accounts, _LEGACY_UNIQUE)
    if legacy_constraint is not None and not _legacy_constraint_is_exact(legacy_constraint, accounts):
        raise RuntimeError(f"Refusing to reuse unexpected constraint {_LEGACY_UNIQUE}")
    if legacy_constraint is None:
        with op.get_context().autocommit_block():
            op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
            op.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")
            try:
                _prepare_legacy_unique_index(accounts)
            finally:
                op.execute("SET statement_timeout = DEFAULT")
                op.execute("SET lock_timeout = DEFAULT")

        op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
        op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
        op.execute(f"ALTER TABLE {accounts_table} ADD CONSTRAINT {_LEGACY_UNIQUE} UNIQUE USING INDEX {_LEGACY_UNIQUE}")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    check = _read_constraint(accounts, _CANONICAL_CHECK)
    if check is not None:
        if check["table_oid"] != accounts["table_oid"] or _constraint_type(check) != "c":
            raise RuntimeError(f"Refusing to drop unexpected constraint {_CANONICAL_CHECK}")
        op.execute(f"ALTER TABLE {accounts_table} DROP CONSTRAINT {_CANONICAL_CHECK}")

    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")
        try:
            _drop_exact_email_index(accounts)
        finally:
            op.execute("SET statement_timeout = DEFAULT")
            op.execute("SET lock_timeout = DEFAULT")

    # Relationship status remains, but durable ordering evidence is lost.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    op.execute(f"ALTER TABLE {wecom_table} DROP COLUMN IF EXISTS event_sequence")
    op.execute(f"ALTER TABLE {wecom_table} DROP COLUMN IF EXISTS event_time")
