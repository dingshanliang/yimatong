"""build launch authority indexes online

Revision ID: u5b1d2e3f4a5
Revises: u5b0c1d2e3f4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5b1d2e3f4a5"
down_revision: str | None = "u5b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_launch_releases_tenant_id_id_idx": (
        "CREATE UNIQUE INDEX uq_launch_releases_tenant_id_id_idx ON public.launch_releases USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_launch_releases_tenant_id_id_idx ON public.launch_releases (tenant_id,id)",
    ),
    "uq_launch_releases_tenant_code_batch_live": (
        "CREATE UNIQUE INDEX uq_launch_releases_tenant_code_batch_live ON public.launch_releases USING btree (tenant_id, code_batch_id) WHERE ((status)::text = 'live'::text)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_launch_releases_tenant_code_batch_live ON public.launch_releases (tenant_id,code_batch_id) WHERE status='live'",
    ),
    "ix_launch_release_actions_tenant_release": (
        "CREATE INDEX ix_launch_release_actions_tenant_release ON public.launch_release_actions USING btree (tenant_id, release_id, created_at)",
        "CREATE INDEX CONCURRENTLY ix_launch_release_actions_tenant_release ON public.launch_release_actions (tenant_id,release_id,created_at)",
    ),
    "ix_launch_releases_tenant_readiness_code_item": (
        "CREATE INDEX ix_launch_releases_tenant_readiness_code_item ON public.launch_releases USING btree (tenant_id, readiness_code_item_id)",
        "CREATE INDEX CONCURRENTLY ix_launch_releases_tenant_readiness_code_item ON public.launch_releases (tenant_id,readiness_code_item_id)",
    ),
}


def _prepare_index(name: str, expected: str, create_sql: str) -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("""
        SELECT indexrelid::regclass::text,indisvalid,pg_get_indexdef(indexrelid)
        FROM pg_index WHERE indexrelid=to_regclass(:name)
    """), {"name": f"public.{name}"}).first()
    if row and row[2] != expected:
        raise RuntimeError(f"existing index {name} has an unexpected definition")
    if row and row[1]:
        return
    if row:
        op.execute(f'DROP INDEX CONCURRENTLY public."{name}"')
    op.execute(create_sql)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, (expected, create_sql) in _INDEXES.items():
            _prepare_index(name, expected, create_sql)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(tuple(_INDEXES)):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
