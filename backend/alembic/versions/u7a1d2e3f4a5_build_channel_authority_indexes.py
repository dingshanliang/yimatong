"""build channel authority indexes online

Revision ID: u7a1d2e3f4a5
Revises: u7a0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "u7a1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u7a0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_distributors_tenant_id_id_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_distributors_tenant_id_id_u7a ON public.distributors USING btree (tenant_id, id)",
    "uq_regions_tenant_id_id_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_regions_tenant_id_id_u7a ON public.regions USING btree (tenant_id, id)",
    "uq_stores_tenant_id_id_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_stores_tenant_id_id_u7a ON public.stores USING btree (tenant_id, id)",
    "uq_channel_receipts_idem_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_channel_receipts_idem_u7a ON public.channel_action_receipts USING btree (tenant_id, action, idempotency_key)",
    "uq_channel_receipts_audit_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_channel_receipts_audit_u7a ON public.channel_action_receipts USING btree (tenant_id, audit_id)",
    "uq_code_alloc_tenant_id_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_code_alloc_tenant_id_u7a ON public.code_allocations USING btree (tenant_id, id)",
    "uq_code_alloc_root_version_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_code_alloc_root_version_u7a ON public.code_allocations USING btree (tenant_id, allocation_root_id, version)",
    "uq_code_alloc_current_root_u7a": "CREATE UNIQUE INDEX CONCURRENTLY uq_code_alloc_current_root_u7a ON public.code_allocations USING btree (tenant_id, allocation_root_id) WHERE effective_to IS NULL",
}


def _prepare(name: str, create_sql: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) AS definition "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=:name"
        ),
        {"name": name},
    ).mappings().one_or_none()
    if row is None:
        return True
    expected = create_sql.replace(" CONCURRENTLY", "")
    actual = row["definition"].replace("CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX").replace(" ON ", " ON ")
    expected_tail = expected.partition(f" {name} ")[2]
    actual_tail = actual.partition(f" {name} ")[2]
    if actual_tail != expected_tail:
        raise RuntimeError(f"same-name index {name} has a non-canonical definition")
    if not row["indisvalid"]:
        op.execute(f'DROP INDEX CONCURRENTLY public."{name}"')
        return True
    return False


def upgrade() -> None:
    for name, sql in _INDEXES.items():
        with op.get_context().autocommit_block():
            if _prepare(name, sql):
                op.execute(sql)


def downgrade() -> None:
    for name in reversed(tuple(_INDEXES)):
        with op.get_context().autocommit_block():
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
