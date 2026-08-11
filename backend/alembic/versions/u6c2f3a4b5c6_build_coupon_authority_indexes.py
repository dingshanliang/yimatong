"""Build coupon authority indexes without blocking writes.

Revision ID: u6c2f3a4b5c6
Revises: u6c1e2f3a4b5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6c2f3a4b5c6"
down_revision: str | None = "u6c1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "uq_coupon_pools_tenant_id_id_idx",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_coupon_pools_tenant_id_id_idx "
        "ON public.coupon_pools USING btree (tenant_id, id)",
    ),
    (
        "uq_coupon_codes_tenant_code_idx",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_coupon_codes_tenant_code_idx "
        "ON public.coupon_codes USING btree (tenant_id, code)",
    ),
    (
        "uq_coupon_codes_tenant_claim",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_coupon_codes_tenant_claim "
        "ON public.coupon_codes USING btree (tenant_id, claim_id) WHERE (claim_id IS NOT NULL)",
    ),
    (
        "ix_coupon_codes_tenant_pool_dist_id",
        "CREATE INDEX CONCURRENTLY ix_coupon_codes_tenant_pool_dist_id "
        "ON public.coupon_codes USING btree (tenant_id, pool_id, distributed, id)",
    ),
)


def _ensure_index(name: str, create_sql: str) -> None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_get_indexdef(indexrelid) AS definition,indisvalid "
                "FROM pg_index WHERE indexrelid=to_regclass(:name)"
            ),
            {"name": f"public.{name}"},
        )
        .mappings()
        .first()
    )
    if row is not None:
        if " ".join(str(row["definition"]).split()) != " ".join(create_sql.replace(" CONCURRENTLY", "").split()):
            raise RuntimeError(f"same-name coupon index has an unexpected definition: {name}")
        if row["indisvalid"]:
            return
        op.execute(f"DROP INDEX CONCURRENTLY public.{name}")
    op.execute(create_sql)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name, create_sql in _INDEXES:
            _ensure_index(name, create_sql)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name, _ in reversed(_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
