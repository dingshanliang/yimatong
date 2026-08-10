"""Finalize the tenant governance contract with bounded metadata locks.

Revision ID: 649cdfd94581
Revises: 648cdfd94580
Create Date: 2026-08-09 22:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "649cdfd94581"
down_revision: str | None = "648cdfd94580"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "30s"
_COORDINATOR_KEY = "yimatong.governance_integrity.migration"
_WRITE_TABLES = (
    "accounts",
    "account_roles",
    "organizations",
    "permissions",
    "role_permissions",
    "roles",
)


def _commit_parent_and_coordinate() -> None:
    # This is the first action: phase B and its Alembic marker become visible
    # before phase C can take locks or fail.  The transaction-level coordinator
    # serializes migration runners only; application DML never takes this lock.
    with op.get_context().autocommit_block():
        op.execute("SELECT 1")
    acquired = op.get_bind().scalar(
        sa.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": _COORDINATOR_KEY}
    )
    if not acquired:
        raise RuntimeError("tenant governance migration coordinator is already held; retry after it completes")


def _lock_writers() -> None:
    tables = ", ".join(f"public.{table}" for table in _WRITE_TABLES)
    op.execute(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE")


def _constraint_row(table: str, constraint: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT constraint_row.contype::text AS contype,
                       constraint_row.convalidated, constraint_row.condeferrable,
                       constraint_row.condeferred, constraint_row.connoinherit,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = constraint_row.conrelid AND attr.attnum = key.attnum
                           ORDER BY key.ordinality
                       ) AS columns,
                       pg_get_expr(constraint_row.conbin, constraint_row.conrelid, false) AS check_expression
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS owner ON owner.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = owner.relnamespace
                WHERE namespace.nspname = 'public' AND owner.relname = :table_name
                  AND constraint_row.conname = :constraint_name
                """
            ),
            {"table_name": table, "constraint_name": constraint},
        )
        .mappings()
        .one_or_none()
    )


def _assert_validated_not_null_premises() -> None:
    for table, constraint in (
        ("account_roles", "ck_account_roles_tenant_id_nn"),
        ("role_permissions", "ck_role_permissions_tenant_id_nn"),
    ):
        row = _constraint_row(table, constraint)
        expression = " ".join(str(row["check_expression"]).split()) if row else ""
        exact = bool(
            row
            and row["contype"] == "c"
            and row["convalidated"]
            and tuple(row["columns"]) == ("tenant_id",)
            and expression in {"tenant_id IS NOT NULL", "(tenant_id IS NOT NULL)"}
            and not row["condeferrable"]
            and not row["condeferred"]
            and not row["connoinherit"]
        )
        if not exact:
            raise RuntimeError(
                f"Cannot finalize governance integrity without exact validated CHECK public.{table}.{constraint}"
            )


def _fail_on_final_drift() -> None:
    rows = [
        dict(row)
        for row in op.get_bind()
        .execute(
            sa.text(
                """
                WITH problems AS (
                    SELECT 'account_roles_null_or_drift' AS kind,
                           mapping.account_id::text AS first_id,
                           mapping.role_id::text AS second_id
                    FROM public.account_roles AS mapping
                    JOIN public.accounts AS account ON account.id = mapping.account_id
                    JOIN public.roles AS role ON role.id = mapping.role_id
                    WHERE mapping.tenant_id IS NULL
                       OR mapping.tenant_id <> account.tenant_id
                       OR mapping.tenant_id <> role.tenant_id
                    UNION ALL
                    SELECT 'role_permissions_null_or_drift', mapping.role_id::text,
                           mapping.permission_id::text
                    FROM public.role_permissions AS mapping
                    JOIN public.roles AS role ON role.id = mapping.role_id
                    JOIN public.permissions AS permission ON permission.id = mapping.permission_id
                    WHERE mapping.tenant_id IS NULL
                       OR mapping.tenant_id <> role.tenant_id
                       OR mapping.tenant_id <> permission.tenant_id
                    UNION ALL
                    SELECT 'account_organization_drift', account.id::text,
                           account.organization_id::text
                    FROM public.accounts AS account
                    JOIN public.organizations AS organization ON organization.id = account.organization_id
                    WHERE account.tenant_id <> organization.tenant_id
                    UNION ALL
                    SELECT 'duplicate_role_name', min(role.id::text), role.name
                    FROM public.roles AS role GROUP BY role.tenant_id, role.name HAVING count(*) > 1
                    UNION ALL
                    SELECT 'duplicate_permission_code', min(permission.id::text), permission.code
                    FROM public.permissions AS permission
                    GROUP BY permission.tenant_id, permission.code HAVING count(*) > 1
                    UNION ALL
                    SELECT 'tenant_without_active_admin', account.tenant_id::text,
                           min(account.id::text)
                    FROM public.accounts AS account
                    GROUP BY account.tenant_id
                    HAVING NOT EXISTS (
                        SELECT 1
                        FROM public.accounts AS active_account
                        JOIN public.account_roles AS mapping ON mapping.account_id = active_account.id
                        JOIN public.roles AS role ON role.id = mapping.role_id
                        WHERE active_account.tenant_id = account.tenant_id
                          AND role.tenant_id = account.tenant_id
                          AND active_account.is_active IS TRUE AND role.name = 'admin'
                    )
                )
                SELECT kind, first_id, second_id FROM problems ORDER BY kind, first_id LIMIT 20
                """
            )
        )
        .mappings()
    ]
    if rows:
        raise RuntimeError(f"Cannot finalize governance integrity; guarded data drift remains: {rows}")

    cycles = [
        dict(row)
        for row in op.get_bind()
        .execute(
            sa.text(
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
                """
            )
        )
        .mappings()
    ]
    if cycles:
        raise RuntimeError(f"Cannot finalize governance integrity; cyclic organizations remain: {cycles}")


def _guarded_bypass() -> str:
    return (
        "(public.current_tenant_id() IS NULL "
        "AND current_setting('app.bypass_rls', true) = 'true' "
        "AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET'))"
    )


def _install_direct_policies() -> None:
    for table in ("account_roles", "role_permissions"):
        expression = f"({table}.tenant_id = public.current_tenant_id() OR {_guarded_bypass()})"
        op.execute(f"DROP POLICY tenant_isolation ON public.{table}")
        op.execute(f"CREATE POLICY tenant_isolation ON public.{table} USING ({expression}) WITH CHECK ({expression})")


def _install_derived_policies() -> None:
    account_expression = (
        "EXISTS (SELECT 1 FROM public.accounts AS owner_account "
        "JOIN public.roles AS owner_role ON owner_role.id = account_roles.role_id "
        "WHERE owner_account.id = account_roles.account_id "
        "AND owner_account.tenant_id = public.current_tenant_id() "
        "AND owner_role.tenant_id = public.current_tenant_id())"
    )
    permission_expression = (
        "EXISTS (SELECT 1 FROM public.roles AS owner_role "
        "JOIN public.permissions AS owner_permission ON owner_permission.id = role_permissions.permission_id "
        "WHERE owner_role.id = role_permissions.role_id "
        "AND owner_role.tenant_id = public.current_tenant_id() "
        "AND owner_permission.tenant_id = public.current_tenant_id())"
    )
    for table, tenant_expression in (
        ("account_roles", account_expression),
        ("role_permissions", permission_expression),
    ):
        expression = f"(({tenant_expression}) OR {_guarded_bypass()})"
        op.execute(f"DROP POLICY tenant_isolation ON public.{table}")
        op.execute(f"CREATE POLICY tenant_isolation ON public.{table} USING ({expression}) WITH CHECK ({expression})")


def upgrade() -> None:
    _commit_parent_and_coordinate()
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _lock_writers()
    # The scan runs with writers fenced by SHARE ROW EXCLUSIVE, not while an
    # ACCESS EXCLUSIVE lock is held.  Validated CHECK constraints let the two
    # following ALTERs be metadata-only after the bounded lock upgrade.
    _assert_validated_not_null_premises()
    _fail_on_final_drift()
    op.execute("ALTER TABLE public.account_roles ALTER COLUMN tenant_id SET NOT NULL")
    op.execute("ALTER TABLE public.role_permissions ALTER COLUMN tenant_id SET NOT NULL")
    _install_direct_policies()


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    acquired = op.get_bind().scalar(
        sa.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": _COORDINATOR_KEY}
    )
    if not acquired:
        raise RuntimeError("tenant governance migration coordinator is already held; retry after it completes")
    _lock_writers()
    _install_derived_policies()
    op.execute("ALTER TABLE public.role_permissions ALTER COLUMN tenant_id DROP NOT NULL")
    op.execute("ALTER TABLE public.account_roles ALTER COLUMN tenant_id DROP NOT NULL")
