"""Harden pilot tenant tables for the restricted runtime role.

Revision ID: fec8dda0b399
Revises: p4e5f6a7b8c9
Create Date: 2026-08-09

The pilot milestone and retrospective tables were added after the runtime RLS
hardening migration.  This forward remediation brings them into the same
ENABLE+FORCE, guarded-bypass and explicit-runtime-grant contract without
rewriting already-applied migration history.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fec8dda0b399"
down_revision: str | None = "p4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "public"
_RUNTIME_ROLE = "yimatong_app"
_TABLES = (
    "pilot_milestones",
    "retrospectives",
    "pilot_milestone_corrections",
)
_PARENT_UNIQUE = "uq_pilot_milestones_tenant_id_id"
_OLD_MILESTONE_FK = "pilot_milestone_corrections_milestone_id_fkey"
_TENANT_MILESTONE_FK = "fk_pilot_milestone_corrections_tenant_milestone"


def _qualified(table_name: str) -> str:
    return f'"{_SCHEMA}"."{table_name}"'


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def _install_guarded_policy(table_name: str) -> None:
    table = _qualified(table_name)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
                AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
            )
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
                AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
            )
        )
        """
    )


def _restore_previous_policy(table_name: str) -> None:
    table = _qualified(table_name)
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def _install_tenant_aware_milestone_fk() -> None:
    bind = op.get_bind()
    mismatched_rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.pilot_milestone_corrections correction "
            "JOIN public.pilot_milestones milestone ON milestone.id = correction.milestone_id "
            "WHERE correction.tenant_id <> milestone.tenant_id"
        )
    ).scalar_one()
    if mismatched_rows:
        raise RuntimeError(
            "pilot milestone corrections contain cross-tenant parent links; "
            "remediate them before applying the tenant-aware foreign key"
        )

    op.create_unique_constraint(
        _PARENT_UNIQUE,
        "pilot_milestones",
        ["tenant_id", "id"],
        schema=_SCHEMA,
    )
    op.drop_constraint(
        _OLD_MILESTONE_FK,
        "pilot_milestone_corrections",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        _TENANT_MILESTONE_FK,
        "pilot_milestone_corrections",
        "pilot_milestones",
        ["tenant_id", "milestone_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="CASCADE",
    )


def _restore_global_milestone_fk() -> None:
    op.drop_constraint(
        _TENANT_MILESTONE_FK,
        "pilot_milestone_corrections",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        _OLD_MILESTONE_FK,
        "pilot_milestone_corrections",
        "pilot_milestones",
        ["milestone_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_constraint(_PARENT_UNIQUE, "pilot_milestones", schema=_SCHEMA, type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _install_tenant_aware_milestone_fk()
    runtime_role_exists = _runtime_role_exists()

    for table_name in _TABLES:
        _install_guarded_policy(table_name)
        if runtime_role_exists:
            table = _qualified(table_name)
            op.execute(f'REVOKE ALL PRIVILEGES ON TABLE {table} FROM "{_RUNTIME_ROLE}"')
            op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO "{_RUNTIME_ROLE}"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    runtime_role_exists = _runtime_role_exists()

    for table_name in reversed(_TABLES):
        if runtime_role_exists:
            op.execute(f'REVOKE ALL PRIVILEGES ON TABLE {_qualified(table_name)} FROM "{_RUNTIME_ROLE}"')
        _restore_previous_policy(table_name)
    _restore_global_milestone_fk()
