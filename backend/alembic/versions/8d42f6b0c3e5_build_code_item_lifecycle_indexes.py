"""Build lifecycle supporting indexes without blocking ordinary writes.

Revision ID: 8d42f6b0c3e5
Revises: 7c31e5a9b2d4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8d42f6b0c3e5"
down_revision: str | None = "7c31e5a9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_code_items_tenant_id_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_code_items_tenant_id_id "
        "ON public.code_items (tenant_id,id)"
    ),
    "ix_interception_records_tenant_code_item": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_interception_records_tenant_code_item "
        "ON public.interception_records (tenant_id,code_item_id)"
    ),
    "ix_code_items_frozen_provenance_backfill": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_code_items_frozen_provenance_backfill "
        "ON public.code_items (id) WHERE status='frozen' AND freeze_provenance_version IS NULL"
    ),
}


def _index_valid(name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            "SELECT idx.indisvalid FROM pg_index idx "
            "WHERE idx.indexrelid=to_regclass('public.' || :name)"
        ),
        {"name": name},
    ).scalar()


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("SET statement_timeout = '10min'")
        for name, create_sql in _INDEXES.items():
            if _index_valid(name) is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            op.execute(create_sql)
        op.execute("RESET lock_timeout")
        op.execute("RESET statement_timeout")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for name in reversed(_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        op.execute("RESET lock_timeout")
