"""Backfill and validate tenant governance online.

Revision ID: 648cdfd94580
Revises: 647cdfd9457f
Create Date: 2026-08-09 22:10:00.000000

Every operation after the compatibility fence is restartable.  Alembic may
leave this revision stamped at its parent after an autocommitted batch or
concurrent index build; NULL rows and catalog validity are the restart markers.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "648cdfd94580"
down_revision: str | None = "647cdfd9457f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "30s"
_BATCH_SIZE = 1_000
_COORDINATOR_KEY = "yimatong.governance_integrity.migration"
_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 0x594D5451554F5441

_UNIQUE_INDEXES = (
    ("roles", "ux_roles_tenant_id_id_online", ("tenant_id", "id"), "uq_roles_tenant_id_id"),
    ("roles", "ux_roles_tenant_name_online", ("tenant_id", "name"), "uq_roles_tenant_name"),
    (
        "permissions",
        "ux_permissions_tenant_id_id_online",
        ("tenant_id", "id"),
        "uq_permissions_tenant_id_id",
    ),
    (
        "permissions",
        "ux_permissions_tenant_code_online",
        ("tenant_id", "code"),
        "uq_permissions_tenant_code",
    ),
)
_TENANT_INDEXES = (
    ("account_roles", "ix_account_roles_tenant_id", ("tenant_id",)),
    ("role_permissions", "ix_role_permissions_tenant_id", ("tenant_id",)),
)
_FOREIGN_KEYS = (
    (
        "accounts",
        "fk_accounts_tenant_organization",
        ("tenant_id", "organization_id"),
        "organizations",
        ("tenant_id", "id"),
    ),
    (
        "account_roles",
        "fk_account_roles_tenant_account",
        ("tenant_id", "account_id"),
        "accounts",
        ("tenant_id", "id"),
    ),
    (
        "account_roles",
        "fk_account_roles_tenant_role",
        ("tenant_id", "role_id"),
        "roles",
        ("tenant_id", "id"),
    ),
    (
        "role_permissions",
        "fk_role_permissions_tenant_role",
        ("tenant_id", "role_id"),
        "roles",
        ("tenant_id", "id"),
    ),
    (
        "role_permissions",
        "fk_role_permissions_tenant_permission",
        ("tenant_id", "permission_id"),
        "permissions",
        ("tenant_id", "id"),
    ),
)
_NOT_NULL_CHECKS = (
    ("account_roles", "ck_account_roles_tenant_id_nn"),
    ("role_permissions", "ck_role_permissions_tenant_id_nn"),
)


def _autocommit(action) -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")
        try:
            action()
        finally:
            op.execute("SET statement_timeout = DEFAULT")
            op.execute("SET lock_timeout = DEFAULT")


def _acquire_coordinator() -> None:
    # This is intentionally the first action.  Entering the autocommit block
    # commits the parent fence and its version marker before validation begins.
    acquired = False

    def acquire() -> None:
        nonlocal acquired
        acquired = bool(
            op.get_bind().scalar(
                sa.text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": _COORDINATOR_KEY},
            )
        )

    _autocommit(acquire)
    if not acquired:
        raise RuntimeError("tenant governance migration coordinator is already held; retry after it completes")


def _release_coordinator() -> None:
    def release() -> None:
        released = op.get_bind().scalar(
            sa.text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": _COORDINATOR_KEY}
        )
        if not released:
            raise RuntimeError("tenant governance migration coordinator ownership was lost")

    _autocommit(release)


def _rows(sql: str) -> list[dict[str, object]]:
    return [dict(row) for row in op.get_bind().execute(sa.text(sql)).mappings()]


def _fail_on_existing_integrity_drift() -> None:
    checks = (
        (
            "tenants with accounts but no active administrator",
            """
            WITH tenant_account_state AS (
                SELECT account.tenant_id, count(*) AS account_count,
                       count(*) FILTER (
                           WHERE account.is_active IS TRUE AND EXISTS (
                               SELECT 1 FROM public.account_roles AS mapping
                               JOIN public.roles AS role ON role.id = mapping.role_id
                               WHERE mapping.account_id = account.id
                                 AND role.tenant_id = account.tenant_id AND role.name = 'admin'
                           )
                       ) AS active_admin_count,
                       array_agg(account.id::text ORDER BY account.id) AS offending_account_ids
                FROM public.accounts AS account GROUP BY account.tenant_id
            )
            SELECT tenant_id::text AS tenant_id, account_count, active_admin_count, offending_account_ids
            FROM tenant_account_state WHERE active_admin_count = 0 ORDER BY tenant_id LIMIT 20
            """,
        ),
        (
            "duplicate tenant role names",
            """
            SELECT tenant_id::text AS tenant_id, name, array_agg(id::text ORDER BY id) AS offending_ids
            FROM public.roles GROUP BY tenant_id, name HAVING count(*) > 1 ORDER BY tenant_id, name LIMIT 20
            """,
        ),
        (
            "duplicate tenant permission codes",
            """
            SELECT tenant_id::text AS tenant_id, code, array_agg(id::text ORDER BY id) AS offending_ids
            FROM public.permissions GROUP BY tenant_id, code HAVING count(*) > 1 ORDER BY tenant_id, code LIMIT 20
            """,
        ),
        (
            "cross-tenant account organization links",
            """
            SELECT account.id::text AS account_id, account.tenant_id::text AS account_tenant_id,
                   organization.id::text AS organization_id,
                   organization.tenant_id::text AS organization_tenant_id
            FROM public.accounts AS account
            JOIN public.organizations AS organization ON organization.id = account.organization_id
            WHERE account.tenant_id <> organization.tenant_id ORDER BY account.id LIMIT 20
            """,
        ),
        (
            "cross-tenant account role links",
            """
            SELECT mapping.account_id::text AS account_id, account.tenant_id::text AS account_tenant_id,
                   mapping.role_id::text AS role_id, role.tenant_id::text AS role_tenant_id
            FROM public.account_roles AS mapping
            JOIN public.accounts AS account ON account.id = mapping.account_id
            JOIN public.roles AS role ON role.id = mapping.role_id
            WHERE account.tenant_id <> role.tenant_id ORDER BY mapping.account_id, mapping.role_id LIMIT 20
            """,
        ),
        (
            "cross-tenant role permission links",
            """
            SELECT mapping.role_id::text AS role_id, role.tenant_id::text AS role_tenant_id,
                   mapping.permission_id::text AS permission_id,
                   permission.tenant_id::text AS permission_tenant_id
            FROM public.role_permissions AS mapping
            JOIN public.roles AS role ON role.id = mapping.role_id
            JOIN public.permissions AS permission ON permission.id = mapping.permission_id
            WHERE role.tenant_id <> permission.tenant_id ORDER BY mapping.role_id, mapping.permission_id LIMIT 20
            """,
        ),
        (
            "cyclic organization hierarchies",
            """
            WITH RECURSIVE organization_walk AS (
                SELECT organization.tenant_id, organization.id AS start_id,
                       organization.id AS current_id, organization.parent_id,
                       ARRAY[organization.id] AS path, false AS cycle
                FROM public.organizations AS organization
                UNION ALL
                SELECT walk.tenant_id, walk.start_id, parent.id, parent.parent_id,
                       walk.path || parent.id, parent.id = ANY(walk.path)
                FROM organization_walk AS walk
                JOIN public.organizations AS parent
                  ON parent.tenant_id = walk.tenant_id AND parent.id = walk.parent_id
                WHERE NOT walk.cycle
            )
            SELECT tenant_id::text AS tenant_id, start_id::text AS organization_id,
                   path::text AS offending_path
            FROM organization_walk WHERE cycle ORDER BY tenant_id, start_id LIMIT 20
            """,
        ),
    )
    for label, sql in checks:
        offending = _rows(sql)
        if offending:
            raise RuntimeError(f"Cannot enforce governance integrity: {label}: {offending}")


def _backfill(table: str, left_table: str, left_key: str, right_table: str, right_key: str) -> None:
    while True:
        updated = 0

        def batch() -> None:
            nonlocal updated
            # Runtime mutations acquire the global rollout lock, then Tenant
            # rows in stable order, and only then business rows.  Preserve that
            # order before SELECT FOR UPDATE on an association mapping; the
            # governance trigger's Tenant lock is therefore re-entrant instead
            # of a mapping -> Tenant inversion.
            op.get_bind().execute(
                sa.text("SELECT pg_advisory_xact_lock_shared(:lock_key)"),
                {"lock_key": _QUOTA_ROLLOUT_ADVISORY_LOCK_KEY},
            )
            updated = int(
                op.get_bind().scalar(
                    sa.text(
                        f"""
                        WITH candidate_tenants AS MATERIALIZED (
                            SELECT DISTINCT lhs.tenant_id
                            FROM public.{table} AS mapping
                            JOIN public.{left_table} AS lhs ON lhs.id = mapping.{left_key}
                            JOIN public.{right_table} AS rhs ON rhs.id = mapping.{right_key}
                            WHERE mapping.tenant_id IS NULL AND lhs.tenant_id = rhs.tenant_id
                            ORDER BY lhs.tenant_id
                            LIMIT :batch_size
                        ), locked_tenants AS MATERIALIZED (
                            SELECT tenant.id
                            FROM public.tenants AS tenant
                            JOIN candidate_tenants AS candidate ON candidate.tenant_id = tenant.id
                            ORDER BY tenant.id
                            FOR UPDATE OF tenant
                        ), batch AS MATERIALIZED (
                            SELECT mapping.ctid
                            FROM public.{table} AS mapping
                            JOIN public.{left_table} AS lhs ON lhs.id = mapping.{left_key}
                            JOIN public.{right_table} AS rhs ON rhs.id = mapping.{right_key}
                            JOIN locked_tenants AS locked_tenant ON locked_tenant.id = lhs.tenant_id
                            WHERE mapping.tenant_id IS NULL AND lhs.tenant_id = rhs.tenant_id
                            ORDER BY mapping.{left_key}, mapping.{right_key}
                            LIMIT :batch_size FOR UPDATE OF mapping SKIP LOCKED
                        ), updated AS (
                            UPDATE public.{table} AS mapping
                            SET tenant_id = lhs.tenant_id
                            FROM batch, public.{left_table} AS lhs, public.{right_table} AS rhs
                            WHERE mapping.ctid = batch.ctid
                              AND lhs.id = mapping.{left_key} AND rhs.id = mapping.{right_key}
                              AND lhs.tenant_id = rhs.tenant_id
                            RETURNING 1
                        ) SELECT count(*) FROM updated
                        """
                    ),
                    {"batch_size": _BATCH_SIZE},
                )
                or 0
            )

        _autocommit(batch)
        if updated == 0:
            break

    remaining = op.get_bind().scalar(sa.text(f"SELECT count(*) FROM public.{table} WHERE tenant_id IS NULL"))
    if remaining:
        raise RuntimeError(
            f"public.{table} still has {remaining} NULL tenant_id rows after a SKIP LOCKED pass; "
            "a row is locked or its endpoints disagree; retry after resolving the exact row"
        )


def _index_row(index_name: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT table_class.relname AS table_name, idx.indisvalid, idx.indisready,
                       idx.indislive, idx.indisunique, idx.indisprimary,
                       idx.indisexclusion, idx.indimmediate, idx.indnullsnotdistinct,
                       idx.indnkeyatts AS key_attribute_count,
                       idx.indnatts AS total_attribute_count,
                       am.amname AS access_method,
                       idx.indpred IS NULL AS has_no_predicate,
                       idx.indexprs IS NULL AS has_no_expressions,
                       idx.indnatts = idx.indnkeyatts AS has_no_includes,
                       NOT EXISTS (
                           SELECT 1
                           FROM unnest(idx.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                           JOIN pg_opclass AS opc ON opc.oid = cls.opclass_oid
                           WHERE cls.ordinality <= idx.indnkeyatts AND NOT opc.opcdefault
                       ) AS uses_default_opclasses,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(idx.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = idx.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= idx.indnkeyatts ORDER BY key.ordinality
                       ) AS key_columns,
                       ARRAY(
                           SELECT option
                           FROM unnest(idx.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                           WHERE opts.ordinality <= idx.indnkeyatts ORDER BY opts.ordinality
                       ) AS key_options,
                       ARRAY(
                           SELECT collation_oid
                           FROM unnest(idx.indcollation::oid[]) WITH ORDINALITY
                             AS collations(collation_oid, ordinality)
                           WHERE collations.ordinality <= idx.indnkeyatts ORDER BY collations.ordinality
                       ) AS key_collations,
                       ARRAY(
                           SELECT attr.attcollation
                           FROM unnest(idx.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = idx.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= idx.indnkeyatts ORDER BY key.ordinality
                       ) AS base_key_collations
                FROM pg_class AS index_class
                JOIN pg_namespace AS ns ON ns.oid = index_class.relnamespace
                JOIN pg_index AS idx ON idx.indexrelid = index_class.oid
                JOIN pg_class AS table_class ON table_class.oid = idx.indrelid
                JOIN pg_am AS am ON am.oid = index_class.relam
                WHERE ns.nspname = 'public' AND index_class.relname = :index_name
                """
            ),
            {"index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )


def _constraint_row(table: str, constraint: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT constraint_row.contype::text AS contype, constraint_row.convalidated,
                       constraint_row.condeferrable, constraint_row.condeferred,
                       constraint_row.connoinherit,
                       constraint_row.confmatchtype::text AS confmatchtype,
                       constraint_row.confupdtype::text AS confupdtype,
                       constraint_row.confdeltype::text AS confdeltype,
                       referenced_ns.nspname AS referenced_schema,
                       referenced.relname AS referenced_table,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = constraint_row.conrelid AND attr.attnum = key.attnum
                           ORDER BY key.ordinality
                       ) AS columns,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(constraint_row.confkey) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = constraint_row.confrelid AND attr.attnum = key.attnum
                           ORDER BY key.ordinality
                       ) AS referenced_columns,
                       pg_get_expr(constraint_row.conbin, constraint_row.conrelid, false) AS check_expression,
                       backing_ns.nspname AS backing_index_schema,
                       backing_index.relname AS backing_index_name,
                       backing_table.relname AS backing_table_name,
                       backing.indisvalid AS backing_indisvalid,
                       backing.indisready AS backing_indisready,
                       backing.indislive AS backing_indislive,
                       backing.indisunique AS backing_indisunique,
                       backing.indisprimary AS backing_indisprimary,
                       backing.indisexclusion AS backing_indisexclusion,
                       backing.indimmediate AS backing_indimmediate,
                       backing.indnullsnotdistinct AS backing_indnullsnotdistinct,
                       backing.indnkeyatts AS backing_key_attribute_count,
                       backing.indnatts AS backing_total_attribute_count,
                       backing_access_method.amname AS backing_access_method,
                       backing.indpred IS NULL AS backing_has_no_predicate,
                       backing.indexprs IS NULL AS backing_has_no_expressions,
                       backing.indnatts = backing.indnkeyatts AS backing_has_no_includes,
                       NOT EXISTS (
                           SELECT 1
                           FROM unnest(backing.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                           JOIN pg_opclass AS opclass ON opclass.oid = cls.opclass_oid
                           WHERE cls.ordinality <= backing.indnkeyatts AND NOT opclass.opcdefault
                       ) AS backing_uses_default_opclasses,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(backing.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = backing.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= backing.indnkeyatts ORDER BY key.ordinality
                       ) AS backing_key_columns,
                       ARRAY(
                           SELECT option
                           FROM unnest(backing.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                           WHERE opts.ordinality <= backing.indnkeyatts ORDER BY opts.ordinality
                       ) AS backing_key_options,
                       ARRAY(
                           SELECT collation_oid
                           FROM unnest(backing.indcollation::oid[]) WITH ORDINALITY
                             AS collations(collation_oid, ordinality)
                           WHERE collations.ordinality <= backing.indnkeyatts ORDER BY collations.ordinality
                       ) AS backing_key_collations,
                       ARRAY(
                           SELECT attr.attcollation
                           FROM unnest(backing.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = backing.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= backing.indnkeyatts ORDER BY key.ordinality
                       ) AS backing_base_key_collations
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS owner ON owner.oid = constraint_row.conrelid
                JOIN pg_namespace AS ns ON ns.oid = owner.relnamespace
                LEFT JOIN pg_class AS referenced ON referenced.oid = constraint_row.confrelid
                LEFT JOIN pg_namespace AS referenced_ns ON referenced_ns.oid = referenced.relnamespace
                LEFT JOIN pg_class AS backing_index ON backing_index.oid = constraint_row.conindid
                LEFT JOIN pg_namespace AS backing_ns ON backing_ns.oid = backing_index.relnamespace
                LEFT JOIN pg_index AS backing ON backing.indexrelid = backing_index.oid
                LEFT JOIN pg_class AS backing_table ON backing_table.oid = backing.indrelid
                LEFT JOIN pg_am AS backing_access_method ON backing_access_method.oid = backing_index.relam
                WHERE ns.nspname = 'public' AND owner.relname = :table_name
                  AND constraint_row.conname = :constraint_name
                """
            ),
            {"table_name": table, "constraint_name": constraint},
        )
        .mappings()
        .one_or_none()
    )


def _validation_matches(row, validated: bool | None) -> bool:
    return validated is None or bool(row["convalidated"]) is validated


def _is_exact_not_null_check(row, *, validated: bool | None) -> bool:
    expression = " ".join(str(row["check_expression"]).split()) if row else ""
    return bool(
        row
        and row["contype"] == "c"
        and tuple(row["columns"]) == ("tenant_id",)
        and expression in {"tenant_id IS NOT NULL", "(tenant_id IS NOT NULL)"}
        and not row["condeferrable"]
        and not row["condeferred"]
        and not row["connoinherit"]
        and _validation_matches(row, validated)
    )


def _is_exact_foreign_key(
    row,
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
    *,
    validated: bool | None,
) -> bool:
    return bool(
        row
        and row["contype"] == "f"
        and tuple(row["columns"]) == columns
        and row["referenced_schema"] == "public"
        and row["referenced_table"] == referenced_table
        and tuple(row["referenced_columns"]) == referenced_columns
        and row["confmatchtype"] == "s"
        and row["confupdtype"] == "a"
        and row["confdeltype"] == "a"
        and not row["condeferrable"]
        and not row["condeferred"]
        and _validation_matches(row, validated)
    )


def _is_exact_unique_constraint(
    row,
    table: str,
    constraint: str,
    columns: tuple[str, ...],
    *,
    validated: bool | None,
) -> bool:
    # Backing indexes use the complete logical fingerprint below. Catalog
    # fields omitted intentionally are indcheckxmin (transient planner safety),
    # indisclustered/indisreplident (table-wide designation), physical
    # tablespace/storage options, and relpersistence (inherited from the table).
    return bool(
        row
        and row["contype"] == "u"
        and tuple(row["columns"]) == columns
        and not row["condeferrable"]
        and not row["condeferred"]
        and _validation_matches(row, validated)
        and row["backing_index_schema"] == "public"
        and row["backing_index_name"] == constraint
        and row["backing_table_name"] == table
        and row["backing_indisvalid"]
        and row["backing_indisready"]
        and row["backing_indislive"]
        and row["backing_indisunique"]
        and not row["backing_indisprimary"]
        and not row["backing_indisexclusion"]
        and row["backing_indimmediate"]
        and not row["backing_indnullsnotdistinct"]
        and row["backing_key_attribute_count"] == len(columns)
        and row["backing_total_attribute_count"] == len(columns)
        and row["backing_access_method"] == "btree"
        and row["backing_has_no_predicate"]
        and row["backing_has_no_expressions"]
        and row["backing_has_no_includes"]
        and row["backing_uses_default_opclasses"]
        and tuple(row["backing_key_columns"]) == columns
        and tuple(row["backing_key_options"]) == (0,) * len(columns)
        and tuple(row["backing_key_collations"]) == tuple(row["backing_base_key_collations"])
    )


def _has_exact_index_shape(
    row,
    table: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> bool:
    # Key identity/order/count, AM, uniqueness mode, opclass, collation,
    # direction/null ordering, predicate/expression/include state and lifecycle
    # flags are all semantic and therefore matched explicitly.
    return bool(
        row
        and row["table_name"] == table
        and bool(row["indisunique"]) is unique
        and not row["indisprimary"]
        and not row["indisexclusion"]
        and row["indimmediate"]
        and not row["indnullsnotdistinct"]
        and row["key_attribute_count"] == len(columns)
        and row["total_attribute_count"] == len(columns)
        and row["access_method"] == "btree"
        and row["has_no_predicate"]
        and row["has_no_expressions"]
        and row["has_no_includes"]
        and row["uses_default_opclasses"]
        and tuple(row["key_columns"]) == columns
        and tuple(row["key_options"]) == (0,) * len(columns)
        and tuple(row["key_collations"]) == tuple(row["base_key_collations"])
    )


def _ensure_index(table: str, index: str, columns: tuple[str, ...], *, unique: bool) -> None:
    row = _index_row(index)
    exact_shape = _has_exact_index_shape(row, table, columns, unique=unique)
    if row is not None and not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        if not exact_shape or not row["indislive"]:
            raise RuntimeError(f"Refusing to drop unexpected invalid index public.{index}")
        op.execute(f"DROP INDEX CONCURRENTLY public.{index}")
        row = None
    if row is not None:
        if not exact_shape:
            raise RuntimeError(f"Refusing to reuse unexpected valid index public.{index}")
        return
    unique_sql = "UNIQUE " if unique else ""
    column_sql = ", ".join(columns)
    op.execute(f"CREATE {unique_sql}INDEX CONCURRENTLY {index} ON public.{table} ({column_sql})")


def _drop_completed_index_if_exact(
    table: str,
    index: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> None:
    row = _index_row(index)
    if row is None:
        return
    exact_completed_index = _has_exact_index_shape(row, table, columns, unique=unique) and all(
        bool(row[state]) for state in ("indisvalid", "indisready", "indislive")
    )
    if not exact_completed_index:
        raise RuntimeError(f"Refusing to drop unexpected index public.{index}")
    op.execute(f"DROP INDEX CONCURRENTLY public.{index}")


def _ensure_unique_constraint(table: str, constraint: str, columns: tuple[str, ...], index: str) -> None:
    row = _constraint_row(table, constraint)
    if row is not None:
        if not _is_exact_unique_constraint(row, table, constraint, columns, validated=True):
            raise RuntimeError(f"Refusing to reuse unexpected constraint public.{table}.{constraint}")
        return
    op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} UNIQUE USING INDEX {index}")
    row = _constraint_row(table, constraint)
    if not _is_exact_unique_constraint(row, table, constraint, columns, validated=True):
        raise RuntimeError(f"Created constraint public.{table}.{constraint} has an unexpected catalog shape")


def _ensure_foreign_key(
    table: str,
    constraint: str,
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> None:
    row = _constraint_row(table, constraint)
    if row is None:
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({', '.join(columns)}) REFERENCES public.{referenced_table} "
            f"({', '.join(referenced_columns)}) NOT VALID"
        )
        row = _constraint_row(table, constraint)
    if not _is_exact_foreign_key(row, columns, referenced_table, referenced_columns, validated=None):
        raise RuntimeError(f"Refusing to reuse unexpected constraint public.{table}.{constraint}")


def _ensure_not_null_check(table: str, constraint: str) -> None:
    row = _constraint_row(table, constraint)
    if row is None:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} CHECK (tenant_id IS NOT NULL) NOT VALID")
        row = _constraint_row(table, constraint)
    if not _is_exact_not_null_check(row, validated=None):
        raise RuntimeError(f"Refusing to reuse unexpected constraint public.{table}.{constraint}")


def _validate_foreign_key(
    table: str,
    constraint: str,
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> None:
    row = _constraint_row(table, constraint)
    if not _is_exact_foreign_key(row, columns, referenced_table, referenced_columns, validated=None):
        raise RuntimeError(f"Refusing to validate unexpected constraint public.{table}.{constraint}")
    if not row["convalidated"]:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {constraint}")
    row = _constraint_row(table, constraint)
    if not _is_exact_foreign_key(row, columns, referenced_table, referenced_columns, validated=True):
        raise RuntimeError(f"Constraint public.{table}.{constraint} did not validate with the expected catalog shape")


def _validate_not_null_check(table: str, constraint: str) -> None:
    row = _constraint_row(table, constraint)
    if not _is_exact_not_null_check(row, validated=None):
        raise RuntimeError(f"Refusing to validate unexpected constraint public.{table}.{constraint}")
    if not row["convalidated"]:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {constraint}")
    row = _constraint_row(table, constraint)
    if not _is_exact_not_null_check(row, validated=True):
        raise RuntimeError(f"Constraint public.{table}.{constraint} did not validate with the expected catalog shape")


def _drop_not_null_check_if_exact(table: str, constraint: str) -> None:
    row = _constraint_row(table, constraint)
    if row is None:
        return
    if not _is_exact_not_null_check(row, validated=True):
        raise RuntimeError(f"Refusing to drop unexpected constraint public.{table}.{constraint}")
    op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")


def _drop_foreign_key_if_exact(
    table: str,
    constraint: str,
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
) -> None:
    row = _constraint_row(table, constraint)
    if row is None:
        return
    if not _is_exact_foreign_key(row, columns, referenced_table, referenced_columns, validated=True):
        raise RuntimeError(f"Refusing to drop unexpected constraint public.{table}.{constraint}")
    op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")


def _drop_unique_constraint_if_exact(
    table: str,
    constraint: str,
    columns: tuple[str, ...],
) -> None:
    row = _constraint_row(table, constraint)
    if row is None:
        return
    if not _is_exact_unique_constraint(row, table, constraint, columns, validated=True):
        raise RuntimeError(f"Refusing to drop unexpected constraint public.{table}.{constraint}")
    op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")


def _upgrade_body() -> None:
    _fail_on_existing_integrity_drift()
    _backfill("account_roles", "accounts", "account_id", "roles", "role_id")
    _backfill("role_permissions", "roles", "role_id", "permissions", "permission_id")
    _fail_on_existing_integrity_drift()

    for table, index, columns, constraint in _UNIQUE_INDEXES:
        if _constraint_row(table, constraint) is None:
            _autocommit(lambda t=table, i=index, c=columns: _ensure_index(t, i, c, unique=True))
        _autocommit(lambda t=table, n=constraint, c=columns, i=index: _ensure_unique_constraint(t, n, c, i))
    for table, index, columns in _TENANT_INDEXES:
        _autocommit(lambda t=table, i=index, c=columns: _ensure_index(t, i, c, unique=False))
    for args in _FOREIGN_KEYS:
        _autocommit(lambda values=args: _ensure_foreign_key(*values))
        _autocommit(lambda values=args: _validate_foreign_key(*values))
    for table, constraint in _NOT_NULL_CHECKS:
        _autocommit(lambda t=table, c=constraint: _ensure_not_null_check(t, c))
        _autocommit(lambda t=table, c=constraint: _validate_not_null_check(t, c))

    remaining = _rows(
        """
        SELECT 'account_roles' AS table_name, count(*) AS null_count
        FROM public.account_roles WHERE tenant_id IS NULL
        UNION ALL
        SELECT 'role_permissions', count(*) FROM public.role_permissions WHERE tenant_id IS NULL
        """
    )
    if any(row["null_count"] for row in remaining):
        raise RuntimeError(f"tenant association backfill is incomplete: {remaining}")


def upgrade() -> None:
    _acquire_coordinator()
    try:
        _upgrade_body()
    finally:
        _release_coordinator()


def downgrade() -> None:
    _acquire_coordinator()
    try:
        for table, constraint in reversed(_NOT_NULL_CHECKS):
            _autocommit(lambda t=table, c=constraint: _drop_not_null_check_if_exact(t, c))
        for args in reversed(_FOREIGN_KEYS):
            _autocommit(lambda values=args: _drop_foreign_key_if_exact(*values))
        for table, _, columns, constraint in reversed(_UNIQUE_INDEXES):
            _autocommit(lambda t=table, c=constraint, cols=columns: _drop_unique_constraint_if_exact(t, c, cols))
        for table, index, columns in reversed(_TENANT_INDEXES):
            _autocommit(lambda t=table, i=index, c=columns: _drop_completed_index_if_exact(t, i, c, unique=False))
    finally:
        _release_coordinator()
