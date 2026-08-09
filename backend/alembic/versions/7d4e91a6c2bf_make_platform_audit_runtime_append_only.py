"""Make the platform audit ledger tenant-scoped and runtime append-only.

Revision ID: 7d4e91a6c2bf
Revises: 263a267db08c
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7d4e91a6c2bf"
down_revision: str | None = "263a267db08c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "platform_audit_log"
_RUNTIME_ROLE = "yimatong_app"
_BYPASS = """
(
    public.current_tenant_id() IS NULL
    AND current_setting('app.bypass_rls', true) = 'true'
    AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
)
"""


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM {_RUNTIME_ROLE}")

    op.execute(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS global_control_principal_only ON public.{_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS audit_tenant_read ON public.{_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS audit_tenant_append ON public.{_TABLE}")

    op.execute(
        f"""
        CREATE POLICY audit_tenant_read ON public.{_TABLE}
        FOR SELECT
        USING (
            target_tenant_id = public.current_tenant_id()::text
            OR {_BYPASS}
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY audit_tenant_append ON public.{_TABLE}
        FOR INSERT
        WITH CHECK (
            target_tenant_id = public.current_tenant_id()::text
            OR EXISTS (
                SELECT 1
                FROM public.agency_authorizations AS agency_auth
                WHERE agency_auth.agency_tenant_id = public.current_tenant_id()
                  AND agency_auth.client_tenant_id::text = platform_audit_log.target_tenant_id
                  AND agency_auth.status = 'active'
                  AND (agency_auth.expires_at IS NULL OR agency_auth.expires_at > CURRENT_TIMESTAMP)
            )
            OR {_BYPASS}
        )
        """
    )
    if _runtime_role_exists():
        op.execute(f"GRANT SELECT, INSERT ON TABLE public.{_TABLE} TO {_RUNTIME_ROLE}")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM {_RUNTIME_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS audit_tenant_append ON public.{_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS audit_tenant_read ON public.{_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS global_control_principal_only ON public.{_TABLE}")
    op.execute(
        f"""
        CREATE POLICY global_control_principal_only ON public.{_TABLE}
        FOR ALL
        USING ({_BYPASS})
        WITH CHECK ({_BYPASS})
        """
    )
