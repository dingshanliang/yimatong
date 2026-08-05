"""add agency authorization rls

Revision ID: b8307c56753e
Revises: s0b1c2d3e4f7
Create Date: 2026-08-03 15:27:11.269679

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8307c56753e"
down_revision: str | None = "s0b1c2d3e4f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.agency_authorizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.agency_authorizations FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_select ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_insert ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_update ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_delete ON public.agency_authorizations")
    op.execute(
        """
        CREATE POLICY agency_authorizations_select ON public.agency_authorizations
        FOR SELECT
        USING (
            agency_tenant_id = public.current_tenant_id()
            OR client_tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY agency_authorizations_insert ON public.agency_authorizations
        FOR INSERT
        WITH CHECK (
            client_tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY agency_authorizations_update ON public.agency_authorizations
        FOR UPDATE
        USING (
            client_tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        WITH CHECK (
            client_tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY agency_authorizations_delete ON public.agency_authorizations
        FOR DELETE
        USING (
            client_tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agency_authorizations_delete ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_update ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_insert ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_select ON public.agency_authorizations")
    op.execute("ALTER TABLE public.agency_authorizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.agency_authorizations DISABLE ROW LEVEL SECURITY")
