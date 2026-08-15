"""build confirmed GMV attribution indexes online

Revision ID: u8b1d2e3f4a5
Revises: u8b0c1d2e3f4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u8b1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u8b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_indexes() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_auth_sessions_tenant_id_id_u8b")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_gmv_attributions_tenant_id_u8b")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_gmv_attributions_confirmed_order_u8b")
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY uq_auth_sessions_tenant_id_id_u8b "
        "ON public.auth_sessions(tenant_id,id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY uq_gmv_attributions_tenant_id_u8b "
        "ON public.gmv_attributions(tenant_id,id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY uq_gmv_attributions_confirmed_order_u8b "
        "ON public.gmv_attributions(tenant_id,external_order_id) WHERE authority_status='confirmed'"
    )


def upgrade() -> None:
    # Each retry removes any invalid catalog shell left by an interrupted CIC.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _rebuild_indexes()
        finally:
            op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_gmv_attributions_confirmed_order_u8b")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_gmv_attributions_tenant_id_u8b")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.uq_auth_sessions_tenant_id_id_u8b")
        finally:
            op.execute("SET lock_timeout = DEFAULT")
