"""Install the committed governance compatibility fence.

Revision ID: 647cdfd9457f
Revises: d076027f6161
Create Date: 2026-08-09 20:22:07.876505

This revision is deliberately scan-free.  Its child revision commits this DDL
before it validates or backfills existing rows, so legacy writers cannot pass
the validation boundary without observing these guards.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "647cdfd9457f"
down_revision: str | None = "d076027f6161"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "5s"
_WRITE_TABLES = (
    "accounts",
    "account_roles",
    "organizations",
    "permissions",
    "role_permissions",
    "roles",
)
_PARTIAL_INDEXES = (
    ("account_roles", "ix_account_roles_tenant_id", ("tenant_id",), False),
    ("role_permissions", "ix_role_permissions_tenant_id", ("tenant_id",), False),
    ("roles", "ux_roles_tenant_id_id_online", ("tenant_id", "id"), True),
    ("roles", "ux_roles_tenant_name_online", ("tenant_id", "name"), True),
    ("permissions", "ux_permissions_tenant_id_id_online", ("tenant_id", "id"), True),
    ("permissions", "ux_permissions_tenant_code_online", ("tenant_id", "code"), True),
)
_PARTIAL_UNIQUES = (
    ("roles", "uq_roles_tenant_id_id", ("tenant_id", "id")),
    ("roles", "uq_roles_tenant_name", ("tenant_id", "name")),
    ("permissions", "uq_permissions_tenant_id_id", ("tenant_id", "id")),
    ("permissions", "uq_permissions_tenant_code", ("tenant_id", "code")),
)
_PARTIAL_FOREIGN_KEYS = (
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


def _lock_writers() -> None:
    # The fixed order is shared by the contract revision.  Waiting here drains
    # pre-fence writers; writers arriving later queue until this revision is
    # committed by the child's first autocommit boundary.
    tables = ", ".join(f"public.{table}" for table in _WRITE_TABLES)
    op.execute(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE")


def _index_row(index_name: str):
    return (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT owner.relname AS table_name, index_row.indisvalid,
                       index_row.indisready, index_row.indislive,
                       index_row.indisunique, index_row.indisprimary,
                       index_row.indisexclusion, index_row.indimmediate,
                       index_row.indnullsnotdistinct,
                       index_row.indnkeyatts AS key_attribute_count,
                       index_row.indnatts AS total_attribute_count,
                       access_method.amname AS access_method,
                       index_row.indpred IS NULL AS has_no_predicate,
                       index_row.indexprs IS NULL AS has_no_expressions,
                       index_row.indnatts = index_row.indnkeyatts AS has_no_includes,
                       NOT EXISTS (
                           SELECT 1
                           FROM unnest(index_row.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                           JOIN pg_opclass AS opclass ON opclass.oid = cls.opclass_oid
                           WHERE cls.ordinality <= index_row.indnkeyatts AND NOT opclass.opcdefault
                       ) AS uses_default_opclasses,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(index_row.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = index_row.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= index_row.indnkeyatts ORDER BY key.ordinality
                       ) AS key_columns,
                       ARRAY(
                           SELECT option
                           FROM unnest(index_row.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                           WHERE opts.ordinality <= index_row.indnkeyatts ORDER BY opts.ordinality
                       ) AS key_options,
                       ARRAY(
                           SELECT collation_oid
                           FROM unnest(index_row.indcollation::oid[]) WITH ORDINALITY
                             AS collations(collation_oid, ordinality)
                           WHERE collations.ordinality <= index_row.indnkeyatts ORDER BY collations.ordinality
                       ) AS key_collations,
                       ARRAY(
                           SELECT attr.attcollation
                           FROM unnest(index_row.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid = index_row.indrelid AND attr.attnum = key.attnum
                           WHERE key.ordinality <= index_row.indnkeyatts ORDER BY key.ordinality
                       ) AS base_key_collations
                FROM pg_class AS index_class
                JOIN pg_namespace AS ns ON ns.oid = index_class.relnamespace
                JOIN pg_index AS index_row ON index_row.indexrelid = index_class.oid
                JOIN pg_class AS owner ON owner.oid = index_row.indrelid
                JOIN pg_am AS access_method ON access_method.oid = index_class.relam
                WHERE ns.nspname = 'public' AND index_class.relname = :index_name
                """
            ),
            {"index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )


def _cleanup_partial_online_indexes() -> None:
    # A failed child revision is still stamped at this fence even though its
    # concurrent index statements have committed.  Clean only exact rollout
    # artifacts; an unexpected same-name index is never silently removed.
    # The fingerprint covers every logical key/uniqueness field. We do not
    # compare indcheckxmin (transient planner safety), indisclustered or
    # indisreplident (table-wide designation), nor tablespace/storage options
    # (physical placement/tuning); relpersistence is inherited from the table.
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        try:
            for table, index, columns, unique in _PARTIAL_INDEXES:
                row = _index_row(index)
                if row is None:
                    continue
                exact = (
                    row["table_name"] == table
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
                # A completed partial index is valid/ready/live. A failed CIC
                # shell may be invalid or not-ready, but it must still be live;
                # indisLive=false means another DROP is in progress and is not
                # safe for this downgrade to claim.
                if not exact or not row["indislive"]:
                    raise RuntimeError(f"Refusing to drop unexpected partial rollout index public.{index}")
                op.execute(f"DROP INDEX CONCURRENTLY public.{index}")
        finally:
            op.execute("SET lock_timeout = DEFAULT")


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


def _is_exact_not_null_check(row) -> bool:
    expression = " ".join(str(row["check_expression"]).split()) if row else ""
    return bool(
        row
        and row["contype"] == "c"
        and tuple(row["columns"]) == ("tenant_id",)
        and expression in {"tenant_id IS NOT NULL", "(tenant_id IS NOT NULL)"}
        and not row["condeferrable"]
        and not row["condeferred"]
        and not row["connoinherit"]
    )


def _is_exact_foreign_key(
    row,
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
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
    )


def _is_exact_unique_constraint(row, table: str, constraint: str, columns: tuple[str, ...]) -> bool:
    # Backing indexes use the same logical fingerprint as standalone rollout
    # indexes; constraint validation and deferrability are checked separately.
    return bool(
        row
        and row["contype"] == "u"
        and tuple(row["columns"]) == columns
        and not row["condeferrable"]
        and not row["condeferred"]
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


def _cleanup_partial_online_constraints() -> None:
    for table, constraint in (
        ("role_permissions", "ck_role_permissions_tenant_id_nn"),
        ("account_roles", "ck_account_roles_tenant_id_nn"),
    ):
        row = _constraint_row(table, constraint)
        if row is not None:
            if not _is_exact_not_null_check(row):
                raise RuntimeError(
                    f"Refusing to drop unexpected partial rollout constraint public.{table}.{constraint}"
                )
            op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")
    for table, constraint, columns, referenced, referenced_columns in reversed(_PARTIAL_FOREIGN_KEYS):
        row = _constraint_row(table, constraint)
        if row is None:
            continue
        if not _is_exact_foreign_key(row, columns, referenced, referenced_columns):
            raise RuntimeError(f"Refusing to drop unexpected partial rollout constraint public.{table}.{constraint}")
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")
    for table, constraint, columns in reversed(_PARTIAL_UNIQUES):
        row = _constraint_row(table, constraint)
        if row is None:
            continue
        if not _is_exact_unique_constraint(row, table, constraint, columns):
            raise RuntimeError(f"Refusing to drop unexpected partial rollout constraint public.{table}.{constraint}")
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")


def _restore_derived_association_policies() -> None:
    guarded_bypass = (
        "(public.current_tenant_id() IS NULL "
        "AND current_setting('app.bypass_rls', true) = 'true' "
        "AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET'))"
    )
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
        expression = f"(({tenant_expression}) OR {guarded_bypass})"
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
        op.execute(f"CREATE POLICY tenant_isolation ON public.{table} USING ({expression}) WITH CHECK ({expression})")


def _install_association_tenant_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.set_account_role_tenant_id()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            account_tenant uuid;
            role_tenant uuid;
        BEGIN
            SELECT tenant_id INTO account_tenant FROM public.accounts WHERE id = NEW.account_id;
            SELECT tenant_id INTO role_tenant FROM public.roles WHERE id = NEW.role_id;
            IF account_tenant IS NULL OR role_tenant IS NULL THEN
                RAISE EXCEPTION 'account_roles endpoints must exist'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF account_tenant <> role_tenant THEN
                RAISE EXCEPTION 'account % and role % belong to different tenants', NEW.account_id, NEW.role_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF NEW.tenant_id IS NOT NULL AND NEW.tenant_id <> account_tenant THEN
                RAISE EXCEPTION 'account_roles tenant_id does not match its endpoints'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            NEW.tenant_id := account_tenant;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER set_account_role_tenant_id
        BEFORE INSERT OR UPDATE OF tenant_id, account_id, role_id ON public.account_roles
        FOR EACH ROW EXECUTE FUNCTION public.set_account_role_tenant_id()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.set_role_permission_tenant_id()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            role_tenant uuid;
            permission_tenant uuid;
        BEGIN
            SELECT tenant_id INTO role_tenant FROM public.roles WHERE id = NEW.role_id;
            SELECT tenant_id INTO permission_tenant FROM public.permissions WHERE id = NEW.permission_id;
            IF role_tenant IS NULL OR permission_tenant IS NULL THEN
                RAISE EXCEPTION 'role_permissions endpoints must exist'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF role_tenant <> permission_tenant THEN
                RAISE EXCEPTION 'role % and permission % belong to different tenants', NEW.role_id, NEW.permission_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF NEW.tenant_id IS NOT NULL AND NEW.tenant_id <> role_tenant THEN
                RAISE EXCEPTION 'role_permissions tenant_id does not match its endpoints'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            NEW.tenant_id := role_tenant;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER set_role_permission_tenant_id
        BEFORE INSERT OR UPDATE OF tenant_id, role_id, permission_id ON public.role_permissions
        FOR EACH ROW EXECUTE FUNCTION public.set_role_permission_tenant_id()
        """
    )


def _install_identity_and_link_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_governance_identity_and_links()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            governed_tenant uuid;
            old_tenant uuid;
        BEGIN
            governed_tenant := CASE WHEN TG_OP = 'DELETE' THEN OLD.tenant_id ELSE NEW.tenant_id END;
            IF TG_OP = 'UPDATE' AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                RAISE EXCEPTION 'tenant ownership is immutable for % rows', TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM 1 FROM public.tenants WHERE id = governed_tenant FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'governed tenant % does not exist', governed_tenant
                    USING ERRCODE = 'foreign_key_violation';
            END IF;

            IF TG_TABLE_NAME = 'accounts' AND TG_OP <> 'DELETE' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.organizations
                    WHERE id = NEW.organization_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'account organization must belong to the account tenant'
                        USING ERRCODE = 'foreign_key_violation';
                END IF;
            ELSIF TG_TABLE_NAME = 'roles' AND TG_OP <> 'DELETE' THEN
                IF EXISTS (
                    SELECT 1 FROM public.roles
                    WHERE tenant_id = NEW.tenant_id AND name = NEW.name AND id <> NEW.id
                ) THEN
                    RAISE EXCEPTION 'duplicate role name % in tenant %', NEW.name, NEW.tenant_id
                        USING ERRCODE = 'unique_violation', CONSTRAINT = 'uq_roles_tenant_name';
                END IF;
            ELSIF TG_TABLE_NAME = 'permissions' AND TG_OP <> 'DELETE' THEN
                IF EXISTS (
                    SELECT 1 FROM public.permissions
                    WHERE tenant_id = NEW.tenant_id AND code = NEW.code AND id <> NEW.id
                ) THEN
                    RAISE EXCEPTION 'duplicate permission code % in tenant %', NEW.code, NEW.tenant_id
                        USING ERRCODE = 'unique_violation', CONSTRAINT = 'uq_permissions_tenant_code';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    for table, events in (
        ("accounts", "INSERT OR DELETE OR UPDATE OF is_active, tenant_id, organization_id"),
        ("roles", "INSERT OR DELETE OR UPDATE OF name, tenant_id"),
        ("permissions", "INSERT OR UPDATE OF code, tenant_id"),
    ):
        op.execute(
            f"CREATE TRIGGER guard_{table}_governance BEFORE {events} ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_governance_identity_and_links()"
        )
    op.execute(
        """
        CREATE FUNCTION public.guard_account_role_governance()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            old_tenant uuid;
            new_tenant uuid;
        BEGIN
            SELECT tenant_id INTO old_tenant FROM public.accounts WHERE id = OLD.account_id;
            IF old_tenant IS NULL THEN
                RAISE EXCEPTION 'account_roles account endpoint must exist'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            PERFORM 1 FROM public.tenants WHERE id = old_tenant FOR UPDATE;
            IF TG_OP = 'UPDATE' THEN
                SELECT tenant_id INTO new_tenant FROM public.accounts WHERE id = NEW.account_id;
                IF new_tenant IS DISTINCT FROM old_tenant THEN
                    RAISE EXCEPTION 'account role grants cannot move between tenants'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            RETURN OLD;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_account_role_governance
        BEFORE DELETE OR UPDATE OF tenant_id, account_id, role_id ON public.account_roles
        FOR EACH ROW EXECUTE FUNCTION public.guard_account_role_governance()
        """
    )


def _install_last_admin_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.assert_tenant_has_active_admin()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            governed_tenant uuid;
            requires_check boolean := false;
        BEGIN
            IF TG_TABLE_NAME = 'account_roles' THEN
                SELECT account.tenant_id,
                       EXISTS (SELECT 1 FROM public.roles WHERE id = OLD.role_id AND name = 'admin')
                INTO governed_tenant, requires_check
                FROM public.accounts AS account WHERE account.id = OLD.account_id;
            ELSIF TG_TABLE_NAME = 'accounts' THEN
                governed_tenant := CASE WHEN TG_OP = 'INSERT' THEN NEW.tenant_id ELSE OLD.tenant_id END;
                requires_check := TG_OP = 'INSERT' OR TG_OP = 'DELETE' OR (OLD.is_active AND NOT NEW.is_active);
            ELSIF TG_TABLE_NAME = 'roles' THEN
                governed_tenant := OLD.tenant_id;
                requires_check := OLD.name = 'admin' AND (TG_OP = 'DELETE' OR NEW.name <> 'admin');
            END IF;
            IF requires_check AND NOT EXISTS (
                SELECT 1
                FROM public.accounts AS account
                JOIN public.account_roles AS mapping ON mapping.account_id = account.id
                JOIN public.roles AS role ON role.id = mapping.role_id
                WHERE account.tenant_id = governed_tenant
                  AND role.tenant_id = governed_tenant
                  AND account.is_active IS TRUE
                  AND role.name = 'admin'
            ) THEN
                RAISE EXCEPTION 'tenant % must retain at least one active administrator', governed_tenant
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $function$
        """
    )
    for table, name, events in (
        ("account_roles", "assert_admin_after_account_role_change", "DELETE OR UPDATE"),
        ("accounts", "assert_admin_after_account_change", "INSERT OR DELETE OR UPDATE"),
        ("roles", "assert_admin_after_role_change", "DELETE OR UPDATE"),
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.assert_tenant_has_active_admin()"
        )


def _install_organization_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_organization_hierarchy()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                RAISE EXCEPTION 'organization tenant ownership is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM 1 FROM public.tenants WHERE id = NEW.tenant_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'organization tenant % does not exist', NEW.tenant_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF NEW.parent_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.organizations
                WHERE id = NEW.parent_id AND tenant_id = NEW.tenant_id
            ) THEN
                RAISE EXCEPTION 'organization parent must belong to the same tenant'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.assert_organization_hierarchy_acyclic()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.parent_id IS NOT NULL AND EXISTS (
                WITH RECURSIVE ancestors(id, parent_id) AS (
                    SELECT id, parent_id FROM public.organizations
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id
                    UNION
                    SELECT organization.id, organization.parent_id
                    FROM public.organizations AS organization
                    JOIN ancestors ON organization.id = ancestors.parent_id
                    WHERE organization.tenant_id = NEW.tenant_id
                )
                SELECT 1 FROM ancestors WHERE id = NEW.id
            ) THEN
                RAISE EXCEPTION 'organization hierarchy cycle detected for organization %', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_organization_hierarchy
        BEFORE INSERT OR UPDATE OF tenant_id, parent_id ON public.organizations
        FOR EACH ROW EXECUTE FUNCTION public.guard_organization_hierarchy()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER assert_organization_hierarchy_acyclic
        AFTER INSERT OR UPDATE ON public.organizations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.assert_organization_hierarchy_acyclic()
        """
    )


def _install_scoped_session_revoker() -> None:
    op.execute(
        """
        CREATE FUNCTION public.revoke_current_tenant_account_sessions(target_account_id uuid)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            tenant_setting text;
            request_tenant uuid;
            revoked_count integer;
        BEGIN
            IF session_user <> 'yimatong_app' THEN
                RAISE EXCEPTION 'session revocation is restricted to the runtime principal'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            tenant_setting := current_setting('app.tenant_id', true);
            IF tenant_setting IS NULL OR tenant_setting = '' OR tenant_setting = 'platform' THEN
                RAISE EXCEPTION 'a tenant UUID context is required for session revocation'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            BEGIN
                request_tenant := tenant_setting::uuid;
            EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'a valid tenant UUID context is required for session revocation'
                    USING ERRCODE = 'insufficient_privilege';
            END;
            IF NOT EXISTS (
                SELECT 1 FROM public.accounts
                WHERE id = target_account_id AND tenant_id = request_tenant
            ) THEN
                RAISE EXCEPTION 'target account is not owned by the current tenant'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            UPDATE public.auth_sessions
            SET revoked_at = COALESCE(revoked_at, clock_timestamp()),
                updated_at = clock_timestamp()
            WHERE account_id = target_account_id
              AND tenant_id = request_tenant
              AND revoked_at IS NULL;
            GET DIAGNOSTICS revoked_count = ROW_COUNT;
            RETURN revoked_count;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) FROM PUBLIC")
    role_exists = op.get_bind().execute(sa.text("SELECT to_regrole('yimatong_app') IS NOT NULL")).scalar_one()
    if role_exists:
        op.execute('GRANT EXECUTE ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) TO "yimatong_app"')


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _lock_writers()
    op.add_column("account_roles", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.add_column("role_permissions", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    _install_association_tenant_guards()
    _install_identity_and_link_guards()
    _install_last_admin_guard()
    _install_organization_guard()
    _install_scoped_session_revoker()


def downgrade() -> None:
    _cleanup_partial_online_indexes()
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _lock_writers()
    _restore_derived_association_policies()
    _cleanup_partial_online_constraints()
    if op.get_bind().execute(sa.text("SELECT to_regrole('yimatong_app') IS NOT NULL")).scalar_one():
        op.execute('REVOKE EXECUTE ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) FROM "yimatong_app"')
    op.execute("DROP FUNCTION public.revoke_current_tenant_account_sessions(uuid)")
    op.execute("DROP TRIGGER assert_organization_hierarchy_acyclic ON public.organizations")
    op.execute("DROP TRIGGER guard_organization_hierarchy ON public.organizations")
    op.execute("DROP FUNCTION public.assert_organization_hierarchy_acyclic()")
    op.execute("DROP FUNCTION public.guard_organization_hierarchy()")
    for table, trigger in (
        ("roles", "assert_admin_after_role_change"),
        ("accounts", "assert_admin_after_account_change"),
        ("account_roles", "assert_admin_after_account_role_change"),
        ("account_roles", "guard_account_role_governance"),
        ("permissions", "guard_permissions_governance"),
        ("roles", "guard_roles_governance"),
        ("accounts", "guard_accounts_governance"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON public.{table}")
    op.execute("DROP FUNCTION public.assert_tenant_has_active_admin()")
    op.execute("DROP FUNCTION public.guard_account_role_governance()")
    op.execute("DROP FUNCTION public.guard_governance_identity_and_links()")
    op.execute("DROP TRIGGER set_role_permission_tenant_id ON public.role_permissions")
    op.execute("DROP FUNCTION public.set_role_permission_tenant_id()")
    op.execute("DROP TRIGGER set_account_role_tenant_id ON public.account_roles")
    op.execute("DROP FUNCTION public.set_account_role_tenant_id()")
    op.drop_column("role_permissions", "tenant_id")
    op.drop_column("account_roles", "tenant_id")
