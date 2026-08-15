"""build export ledger authority indexes online

Revision ID: u8c2b3c4d5e6
Revises: u8c1a2b3c4d5
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u8c2b3c4d5e6"
down_revision: str | Sequence[str] | None = "u8c1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_DEFINITIONS = (
    (
        "uq_export_logs_tenant_idempotency_u8c",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_export_logs_tenant_idempotency_u8c "
        "ON public.export_logs(tenant_id,idempotency_key)",
    ),
    (
        "ix_export_logs_tenant_auth_session_u8c",
        "CREATE INDEX CONCURRENTLY ix_export_logs_tenant_auth_session_u8c "
        "ON public.export_logs(tenant_id,auth_session_id) WHERE auth_session_id IS NOT NULL",
    ),
    (
        "ix_gmv_attr_confirmations_tenant_auth_session_u8c",
        "CREATE INDEX CONCURRENTLY ix_gmv_attr_confirmations_tenant_auth_session_u8c "
        "ON public.gmv_attribution_confirmations(tenant_id,auth_session_id)",
    ),
)


def upgrade() -> None:
    # Retrying after an interrupted CREATE INDEX CONCURRENTLY removes the
    # invalid catalog shell before rebuilding it.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        try:
            for index_name, definition in _INDEX_DEFINITIONS:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
                op.execute(definition)
        finally:
            op.execute("SET lock_timeout=DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        try:
            for index_name, _definition in reversed(_INDEX_DEFINITIONS):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
        finally:
            op.execute("SET lock_timeout=DEFAULT")
