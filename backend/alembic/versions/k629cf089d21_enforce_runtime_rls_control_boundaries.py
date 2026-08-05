"""Enforce runtime RLS and control-plane privilege boundaries.

Revision ID: k629cf089d21
Revises: b201eb830d00
Create Date: 2026-08-03 23:10:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k629cf089d21"
down_revision: str | None = "b201eb830d00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "public"
_BACKUP_TABLE = "rls_force_remediation_backups"
_PRIV_BACKUP_TABLE = "runtime_privilege_remediation_backup"
_RUNTIME_ROLE = "yimatong_app"
_BYPASS_PARAMETER = "app.bypass_rls"
_PARTITION_FUNCTION = "create_secure_scan_events_partition"

# Relations which were historically added without an RLS policy.  The first
# group carries its owner directly; the second derives ownership through the
# authoritative parent relation instead of inventing a duplicate tenant_id.
_DIRECT_TENANT_TABLES = (
    "ai_generations",
    "code_allocations",
    "diversion_evidence",
    "diversion_investigation_history",
    "gmv_daily_stats",
    "risk_notifications",
    "sync_mappings",
    "tenant_domains",
)
_DERIVED_TENANT_EXPRESSIONS = {
    "coupon_codes": (
        "EXISTS (SELECT 1 FROM public.coupon_pools AS owner "
        "WHERE owner.id = coupon_codes.pool_id "
        "AND owner.tenant_id = public.current_tenant_id())"
    ),
    "regional_code_rules": (
        "EXISTS (SELECT 1 FROM public.regional_orgs AS owner "
        "WHERE owner.id = regional_code_rules.org_id "
        "AND owner.tenant_id = public.current_tenant_id())"
    ),
    "regional_templates": (
        "EXISTS (SELECT 1 FROM public.regional_orgs AS owner "
        "WHERE owner.id = regional_templates.org_id "
        "AND owner.tenant_id = public.current_tenant_id())"
    ),
    "role_permissions": (
        "EXISTS (SELECT 1 FROM public.roles AS owner_role "
        "JOIN public.permissions AS owner_permission "
        "ON owner_permission.id = role_permissions.permission_id "
        "WHERE owner_role.id = role_permissions.role_id "
        "AND owner_role.tenant_id = public.current_tenant_id() "
        "AND owner_permission.tenant_id = public.current_tenant_id())"
    ),
    "whitelabel_configs": (
        "EXISTS (SELECT 1 FROM public.regional_orgs AS owner "
        "WHERE owner.id = whitelabel_configs.org_id "
        "AND owner.tenant_id = public.current_tenant_id())"
    ),
    "account_roles": (
        "EXISTS (SELECT 1 FROM public.accounts AS owner_account "
        "JOIN public.roles AS owner_role "
        "ON owner_role.id = account_roles.role_id "
        "WHERE owner_account.id = account_roles.account_id "
        "AND owner_account.tenant_id = public.current_tenant_id() "
        "AND owner_role.tenant_id = public.current_tenant_id())"
    ),
}
_BUSINESS_POLICY_TABLES = (*_DIRECT_TENANT_TABLES, *_DERIVED_TENANT_EXPRESSIONS)
_CREATED_INDEXES = (
    ("ix_account_roles_role_id", "account_roles", ("role_id",)),
    ("ix_role_permissions_permission_id", "role_permissions", ("permission_id",)),
)

# These tables are platform/security control state, not tenant runtime data.
_GLOBAL_CONTROL_TABLES = (
    "platform_tenant_openings",
    "consumed_refresh_tokens",
    "invite_registration_receipts",
    "role_template_backups",
    "organization_parent_repair_backups",
    "tenant_platform_role_assignment_backups",
    "platform_audit_log",
    "platform_configs",
    "tenant_invite_codes",
)
_OPERATOR_LEDGER = "operator_campaign_manage_grants"
_ALL_CONTROL_TABLES = (*_GLOBAL_CONTROL_TABLES, _OPERATOR_LEDGER, _BACKUP_TABLE, _PRIV_BACKUP_TABLE)


def _quote(identifier: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote_identifier(identifier)


def _qualified(table_name: str) -> str:
    return f"{_quote(_SCHEMA)}.{_quote(table_name)}"


def _assert_public_table(table_name: str) -> None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT cls.oid
            FROM pg_class AS cls
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE cls.oid = to_regclass(:qualified_name)
              AND ns.nspname = :schema_name
              AND cls.relname = :table_name
              AND cls.relkind IN ('r', 'p')
                """
            ),
            {
                "qualified_name": f"{_SCHEMA}.{table_name}",
                "schema_name": _SCHEMA,
                "table_name": table_name,
            },
        )
        .scalar_one_or_none()
    )
    if row is None:
        raise RuntimeError(f"Required table {_SCHEMA}.{table_name} does not exist")


def _policy_snapshot(table_name: str) -> list[dict[str, object]]:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT pol.polname,
                   pol.polpermissive,
                   pol.polcmd,
                   ARRAY(
                       SELECT CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE roles.rolname END
                       FROM unnest(pol.polroles) AS role_oid
                       LEFT JOIN pg_roles AS roles ON roles.oid = role_oid
                       ORDER BY role_oid
                   ) AS role_names,
                   pg_get_expr(pol.polqual, pol.polrelid) AS using_expression,
                   pg_get_expr(pol.polwithcheck, pol.polrelid) AS check_expression
            FROM pg_policy AS pol
            WHERE pol.polrelid = to_regclass(:qualified_name)
            ORDER BY pol.polname
                """
            ),
            {"qualified_name": f"{_SCHEMA}.{table_name}"},
        )
        .mappings()
    )
    return [dict(row) for row in rows]


def _json_default(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("ascii")
    raise TypeError(f"Unsupported policy snapshot value: {type(value).__name__}")


def _snapshot_table(table_name: str) -> None:
    _assert_public_table(table_name)
    state = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT cls.relrowsecurity, cls.relforcerowsecurity
            FROM pg_class AS cls
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE cls.oid = to_regclass(:qualified_name)
              AND ns.nspname = :schema_name
                """
            ),
            {"qualified_name": f"{_SCHEMA}.{table_name}", "schema_name": _SCHEMA},
        )
        .one()
    )
    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO {_qualified(_BACKUP_TABLE)}
                (table_name, rls_enabled, rls_forced, policy_snapshot)
            VALUES (:table_name, :rls_enabled, :rls_forced, CAST(:policy_snapshot AS jsonb))
            ON CONFLICT (table_name) DO NOTHING
            """
        ),
        {
            "table_name": table_name,
            "rls_enabled": state.relrowsecurity,
            "rls_forced": state.relforcerowsecurity,
            "policy_snapshot": json.dumps(_policy_snapshot(table_name), default=_json_default),
        },
    )


def _drop_all_policies(table_name: str) -> None:
    names = (
        op.get_bind()
        .execute(
            sa.text("SELECT polname FROM pg_policy WHERE polrelid = to_regclass(:qualified_name) ORDER BY polname"),
            {"qualified_name": f"{_SCHEMA}.{table_name}"},
        )
        .scalars()
    )
    for policy_name in names:
        op.execute(f"DROP POLICY {_quote(policy_name)} ON {_qualified(table_name)}")


def _install_global_control_policy(table_name: str) -> None:
    _assert_public_table(table_name)
    table = _qualified(table_name)
    _drop_all_policies(table_name)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY global_control_principal_only ON {table}
        USING (
            public.current_tenant_id() IS NULL
            AND current_setting('{_BYPASS_PARAMETER}', true) = 'true'
            AND has_parameter_privilege(session_user, '{_BYPASS_PARAMETER}', 'SET')
        )
        WITH CHECK (
            public.current_tenant_id() IS NULL
            AND current_setting('{_BYPASS_PARAMETER}', true) = 'true'
            AND has_parameter_privilege(session_user, '{_BYPASS_PARAMETER}', 'SET')
        )
        """
    )


def _guarded_control_expression() -> str:
    return (
        "(public.current_tenant_id() IS NULL "
        f"AND current_setting('{_BYPASS_PARAMETER}', true) = 'true' "
        f"AND has_parameter_privilege(session_user, '{_BYPASS_PARAMETER}', 'SET'))"
    )


def _install_business_policy(table_name: str, tenant_expression: str) -> None:
    """Install one reversible, bidirectional tenant policy on a business relation."""

    _assert_public_table(table_name)
    table = _qualified(table_name)
    _drop_all_policies(table_name)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    expression = f"(({tenant_expression}) OR {_guarded_control_expression()})"
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expression}) WITH CHECK ({expression})")


def _scan_event_partitions() -> list[str]:
    return list(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT child.relname
                FROM pg_inherits AS inh
                JOIN pg_class AS parent ON parent.oid = inh.inhparent
                JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                JOIN pg_class AS child ON child.oid = inh.inhrelid
                JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
                WHERE parent_ns.nspname = 'public'
                  AND parent.relname = 'scan_events'
                  AND child_ns.nspname = 'public'
                ORDER BY child.relname
                """
            )
        )
        .scalars()
    )


def _install_scan_partition_policy(table_name: str) -> None:
    table = _qualified(table_name)
    _drop_all_policies(table_name)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('{_BYPASS_PARAMETER}', true) = 'true'
                AND has_parameter_privilege(session_user, '{_BYPASS_PARAMETER}', 'SET')
            )
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('{_BYPASS_PARAMETER}', true) = 'true'
                AND has_parameter_privilege(session_user, '{_BYPASS_PARAMETER}', 'SET')
            )
        )
        """
    )


def _install_scan_partition_lifecycle() -> None:
    """Install the only supported future scan partition creation path.

    The child is created as a standalone relation, hardened, and only then
    attached and granted to the runtime role, all in one transaction.
    """
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.{_PARTITION_FUNCTION}(partition_start date)
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            partition_end date := (partition_start + INTERVAL '1 month')::date;
            partition_name text := 'scan_events_' || to_char(partition_start, 'YYYY_MM');
            qualified_partition text := format('public.%I', partition_name);
        BEGIN
            IF partition_start <> date_trunc('month', partition_start)::date THEN
                RAISE EXCEPTION 'partition_start must be the first day of a month';
            END IF;
            IF partition_name !~ '^scan_events_[0-9]{{4}}_[0-9]{{2}}$' THEN
                RAISE EXCEPTION 'invalid scan partition name';
            END IF;

            EXECUTE format(
                'CREATE TABLE %s (LIKE public.scan_events INCLUDING DEFAULTS '
                'INCLUDING CONSTRAINTS INCLUDING INDEXES)',
                qualified_partition
            );
            EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', qualified_partition);
            EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', qualified_partition);
            EXECUTE format(
                'CREATE POLICY tenant_isolation ON %s USING ('
                'tenant_id = public.current_tenant_id() OR ('
                'public.current_tenant_id() IS NULL AND '
                'current_setting(''app.bypass_rls'', true) = ''true'' AND '
                'has_parameter_privilege(session_user, ''app.bypass_rls'', ''SET''))) '
                'WITH CHECK ('
                'tenant_id = public.current_tenant_id() OR ('
                'public.current_tenant_id() IS NULL AND '
                'current_setting(''app.bypass_rls'', true) = ''true'' AND '
                'has_parameter_privilege(session_user, ''app.bypass_rls'', ''SET'')))',
                qualified_partition
            );
            EXECUTE format(
                'ALTER TABLE public.scan_events ATTACH PARTITION %s FOR VALUES FROM (%L) TO (%L)',
                qualified_partition, partition_start, partition_end
            );
            -- Runtime SQL always goes through the partitioned parent.  Child
            -- relations intentionally keep zero direct runtime ACL so replay
            -- and lifecycle creation have the same fail-closed contract.
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_RUNTIME_ROLE}') THEN
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM {_RUNTIME_ROLE}', qualified_partition);
            END IF;
            RETURN partition_name;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_PARTITION_FUNCTION}(date) FROM PUBLIC")


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": _RUNTIME_ROLE}).scalar()
    )


def _runtime_relation_privileges() -> list[dict[str, object]]:
    if not _runtime_role_exists():
        return []
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT cls.relname AS table_name,
                   privilege_type,
                   is_grantable
            FROM pg_class AS cls
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('r', cls.relowner))) AS acl
            JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE ns.nspname = :schema_name
              AND cls.relname = ANY(CAST(:table_names AS text[]))
              AND grantee.rolname = :runtime_role
            ORDER BY cls.relname, privilege_type, is_grantable
            """
            ),
            {
                "schema_name": _SCHEMA,
                "table_names": list((*_GLOBAL_CONTROL_TABLES, _OPERATOR_LEDGER, "alembic_version")),
                "runtime_role": _RUNTIME_ROLE,
            },
        )
        .mappings()
    )
    return [dict(row) for row in rows]


def _runtime_default_privileges(migration_role: str) -> list[dict[str, object]]:
    if not _runtime_role_exists():
        return []
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT privilege_type, is_grantable
            FROM pg_default_acl AS defaults
            JOIN pg_roles AS owner ON owner.oid = defaults.defaclrole
            JOIN pg_namespace AS ns ON ns.oid = defaults.defaclnamespace
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
            JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE owner.rolname = :owner_role
              AND ns.nspname = :schema_name
              AND defaults.defaclobjtype = 'r'
              AND grantee.rolname = :runtime_role
            ORDER BY privilege_type, is_grantable
            """
            ),
            {"owner_role": migration_role, "schema_name": _SCHEMA, "runtime_role": _RUNTIME_ROLE},
        )
        .mappings()
    )
    return [dict(row) for row in rows]


def _parameter_privileges() -> list[dict[str, object]]:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END AS grantee,
                   acl.privilege_type,
                   acl.is_grantable
            FROM pg_parameter_acl AS parameter_acl
            CROSS JOIN LATERAL aclexplode(parameter_acl.paracl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE parameter_acl.parname = :parameter_name
            ORDER BY grantee, privilege_type, is_grantable
            """
            ),
            {"parameter_name": _BYPASS_PARAMETER},
        )
        .mappings()
    )
    return [dict(row) for row in rows]


def _snapshot_privileges() -> None:
    migration_role = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO {_qualified(_PRIV_BACKUP_TABLE)}
                (id, migration_role, relation_acl, default_acl, parameter_acl)
            VALUES (1, :migration_role, CAST(:relation_acl AS jsonb),
                    CAST(:default_acl AS jsonb), CAST(:parameter_acl AS jsonb))
            """
        ),
        {
            "migration_role": migration_role,
            "relation_acl": json.dumps(_runtime_relation_privileges()),
            "default_acl": json.dumps(_runtime_default_privileges(migration_role)),
            "parameter_acl": json.dumps(_parameter_privileges()),
        },
    )


def _grant_privileges(prefix: str, principal: str, grants: list[dict[str, object]]) -> None:
    grouped: dict[bool, list[str]] = {False: [], True: []}
    for grant in grants:
        grouped[bool(grant["is_grantable"])].append(str(grant["privilege_type"]))
    for grantable, privileges in grouped.items():
        if not privileges:
            continue
        suffix = " WITH GRANT OPTION" if grantable else ""
        op.execute(f"GRANT {', '.join(sorted(set(privileges)))} {prefix} TO {principal}{suffix}")


def _restore_privileges() -> None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT migration_role, relation_acl, default_acl, parameter_acl "
                f"FROM {_qualified(_PRIV_BACKUP_TABLE)} WHERE id = 1"
            )
        )
        .mappings()
        .one()
    )
    migration_role = str(row["migration_role"])

    # Remove only the grants this migration changed, then reconstruct the
    # pre-upgrade catalog projection. REVOKE of the final non-default entry
    # removes the catalog ACL row, preserving the original "no row" state.
    op.execute(f'REVOKE ALL ON PARAMETER "{_BYPASS_PARAMETER}" FROM PUBLIC')
    op.execute(f'REVOKE ALL ON PARAMETER "{_BYPASS_PARAMETER}" FROM {_quote(migration_role)}')
    parameter_by_grantee: dict[str, list[dict[str, object]]] = {}
    for grant in row["parameter_acl"]:
        parameter_by_grantee.setdefault(str(grant["grantee"]), []).append(grant)
    for grantee, grants in parameter_by_grantee.items():
        principal = "PUBLIC" if grantee == "PUBLIC" else _quote(grantee)
        _grant_privileges(f'ON PARAMETER "{_BYPASS_PARAMETER}"', principal, grants)

    if _runtime_role_exists():
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(migration_role)} IN SCHEMA public "
            f"REVOKE ALL ON TABLES FROM {_quote(_RUNTIME_ROLE)}"
        )
        default_grants = list(row["default_acl"])
        grouped: dict[bool, list[str]] = {False: [], True: []}
        for grant in default_grants:
            grouped[bool(grant["is_grantable"])].append(str(grant["privilege_type"]))
        for grantable, privileges in grouped.items():
            if privileges:
                suffix = " WITH GRANT OPTION" if grantable else ""
                op.execute(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(migration_role)} IN SCHEMA public "
                    f"GRANT {', '.join(sorted(set(privileges)))} ON TABLES TO {_quote(_RUNTIME_ROLE)}{suffix}"
                )

        relation_by_table: dict[str, list[dict[str, object]]] = {}
        for grant in row["relation_acl"]:
            relation_by_table.setdefault(str(grant["table_name"]), []).append(grant)
        for table_name in (*_GLOBAL_CONTROL_TABLES, _OPERATOR_LEDGER, "alembic_version"):
            op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_qualified(table_name)} FROM {_quote(_RUNTIME_ROLE)}")
            _grant_privileges(
                f"ON TABLE {_qualified(table_name)}",
                _quote(_RUNTIME_ROLE),
                relation_by_table.get(table_name, []),
            )


def _secure_runtime_privileges() -> None:
    migration_role = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    op.execute(f'REVOKE SET ON PARAMETER "{_BYPASS_PARAMETER}" FROM PUBLIC')
    op.execute(f'GRANT SET ON PARAMETER "{_BYPASS_PARAMETER}" TO {_quote(migration_role)}')
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(migration_role)} IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC"
    )
    if not _runtime_role_exists():
        return
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(migration_role)} IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {_quote(_RUNTIME_ROLE)}"
    )
    for table_name in _ALL_CONTROL_TABLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_qualified(table_name)} FROM {_quote(_RUNTIME_ROLE)}")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_qualified('alembic_version')} FROM {_quote(_RUNTIME_ROLE)}")
    for table_name in _scan_event_partitions():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_qualified(table_name)} FROM {_quote(_RUNTIME_ROLE)}")


def _as_text(value: object) -> str:
    return value.decode("ascii") if isinstance(value, bytes) else str(value)


def _restore_policy(table_name: str, policy: dict[str, object]) -> None:
    command = {"*": "ALL", "r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE"}[_as_text(policy["polcmd"])]
    roles = policy.get("role_names") or ["PUBLIC"]
    role_sql = ", ".join("PUBLIC" if _as_text(role) == "PUBLIC" else _quote(_as_text(role)) for role in roles)
    sql = (
        f"CREATE POLICY {_quote(str(policy['polname']))} ON {_qualified(table_name)} "
        f"AS {'PERMISSIVE' if policy['polpermissive'] else 'RESTRICTIVE'} FOR {command} TO {role_sql}"
    )
    if policy.get("using_expression") is not None:
        sql += f" USING ({policy['using_expression']})"
    if policy.get("check_expression") is not None:
        sql += f" WITH CHECK ({policy['check_expression']})"
    op.execute(sql)


def _restore_table_state(
    table_name: str,
    rls_enabled: bool,
    rls_forced: bool,
    policies: list[dict[str, object]],
) -> None:
    _drop_all_policies(table_name)
    for policy in policies:
        _restore_policy(table_name, policy)
    table = _qualified(table_name)
    op.execute(f"ALTER TABLE {table} {'ENABLE' if rls_enabled else 'DISABLE'} ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} {'FORCE' if rls_forced else 'NO FORCE'} ROW LEVEL SECURITY")


def _harden_all_bypass_policies() -> None:
    """Make a runtime-set custom GUC inert unless the DB principal owns SET authority.

    PostgreSQL accepts arbitrary custom GUC names even when a parameter ACL is
    present. The ACL check therefore belongs inside every bypass policy too.
    """

    marker = "current_setting('app.bypass_rls'::text, true) = 'true'::text"
    guard = (
        "(current_setting('app.bypass_rls'::text, true) = 'true'::text "
        "AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET'))"
    )
    tables = list(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT DISTINCT cls.relname
                FROM pg_policy AS pol
                JOIN pg_class AS cls ON cls.oid = pol.polrelid
                JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = 'public'
                  AND (
                    COALESCE(pg_get_expr(pol.polqual, pol.polrelid), '') LIKE '%app.bypass_rls%'
                    OR COALESCE(pg_get_expr(pol.polwithcheck, pol.polrelid), '') LIKE '%app.bypass_rls%'
                  )
                ORDER BY cls.relname
                """
            )
        )
        .scalars()
    )
    for table_name in tables:
        _snapshot_table(table_name)
        policies = _policy_snapshot(table_name)
        changed = False
        for policy in policies:
            for key in ("using_expression", "check_expression"):
                expression = policy.get(key)
                if expression is None or "has_parameter_privilege" in _as_text(expression):
                    continue
                replaced = _as_text(expression).replace(marker, guard)
                if replaced != expression:
                    policy[key] = replaced
                    changed = True
        if changed:
            _drop_all_policies(table_name)
            for policy in policies:
                _restore_policy(table_name, policy)


def upgrade() -> None:
    # Bound lock acquisition before any relation/RLS DDL. A timeout aborts the
    # Alembic transaction without partial catalog state and is safe to retry.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        _BACKUP_TABLE,
        sa.Column("table_name", sa.String(length=63), nullable=False),
        sa.Column("rls_enabled", sa.Boolean(), nullable=False),
        sa.Column("rls_forced", sa.Boolean(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("table_name"),
        schema=_SCHEMA,
    )
    op.create_table(
        _PRIV_BACKUP_TABLE,
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("migration_role", sa.String(length=63), nullable=False),
        sa.Column("relation_acl", sa.JSON(), nullable=False),
        sa.Column("default_acl", sa.JSON(), nullable=False),
        sa.Column("parameter_acl", sa.JSON(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_runtime_privilege_backup_singleton"),
        sa.PrimaryKeyConstraint("id"),
        schema=_SCHEMA,
    )
    # The privilege snapshot is migration rollback state.  It can describe
    # grants which no longer exist, so exposing it to the runtime role would
    # disclose control-plane history and make the backup itself a new control
    # surface.  Keep it deny-by-default from the instant it is created.
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_qualified(_PRIV_BACKUP_TABLE)} FROM {_quote(_RUNTIME_ROLE)}")
    _snapshot_privileges()

    for table_name in (*_GLOBAL_CONTROL_TABLES, _OPERATOR_LEDGER):
        _snapshot_table(table_name)
    for table_name in _BUSINESS_POLICY_TABLES:
        _snapshot_table(table_name)
    partitions = _scan_event_partitions()
    for table_name in partitions:
        _snapshot_table(table_name)

    # Close the table-owner bypass for every migration-owned RLS relation.
    unforced_tables = list(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT cls.relname
                FROM pg_class AS cls
                JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = 'public'
                  AND cls.relkind IN ('r', 'p')
                  AND cls.relrowsecurity
                  AND NOT cls.relforcerowsecurity
                ORDER BY cls.relname
                """
            )
        )
        .scalars()
    )
    for table_name in unforced_tables:
        _snapshot_table(table_name)
        op.execute(f"ALTER TABLE {_qualified(table_name)} FORCE ROW LEVEL SECURITY")

    _harden_all_bypass_policies()
    for table_name in _DIRECT_TENANT_TABLES:
        _install_business_policy(table_name, f"{table_name}.tenant_id = public.current_tenant_id()")
    for table_name, tenant_expression in _DERIVED_TENANT_EXPRESSIONS.items():
        _install_business_policy(table_name, tenant_expression)
    for index_name, table_name, columns in _CREATED_INDEXES:
        op.create_index(index_name, table_name, list(columns), unique=False, schema=_SCHEMA)
    for table_name in _ALL_CONTROL_TABLES:
        _install_global_control_policy(table_name)
    for table_name in partitions:
        _install_scan_partition_policy(table_name)
    _install_scan_partition_lifecycle()

    _secure_runtime_privileges()


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.execute("SELECT set_config('app.tenant_id', '', true)")
    op.execute(f"SELECT set_config('{_BYPASS_PARAMETER}', 'true', true)")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_PARTITION_FUNCTION}(date)")
    for index_name, table_name, _columns in reversed(_CREATED_INDEXES):
        op.drop_index(index_name, table_name=table_name, schema=_SCHEMA)

    rows = list(
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT table_name, rls_enabled, rls_forced, policy_snapshot "
                f"FROM {_qualified(_BACKUP_TABLE)} ORDER BY table_name"
            )
        )
        .mappings()
    )
    for row in rows:
        _restore_table_state(
            row["table_name"],
            row["rls_enabled"],
            row["rls_forced"],
            row["policy_snapshot"],
        )

    _restore_privileges()

    # The backup relations did not exist before this revision.
    _drop_all_policies(_BACKUP_TABLE)
    op.drop_table(_BACKUP_TABLE, schema=_SCHEMA)
    _drop_all_policies(_PRIV_BACKUP_TABLE)
    op.drop_table(_PRIV_BACKUP_TABLE, schema=_SCHEMA)
