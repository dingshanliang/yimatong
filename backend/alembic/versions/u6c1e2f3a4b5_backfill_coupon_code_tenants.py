"""Backfill coupon-code tenants in bounded committed batches.

Revision ID: u6c1e2f3a4b5
Revises: u6c0d1e2f3a4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6c1e2f3a4b5"
down_revision: str | None = "u6c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 1000


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        while True:
            updated = op.get_bind().execute(
                sa.text(
                    "WITH batch AS (SELECT code.id,pool.tenant_id FROM public.coupon_codes AS code "
                    "JOIN public.coupon_pools AS pool ON pool.id=code.pool_id "
                    "WHERE code.tenant_id IS NULL ORDER BY code.id FOR UPDATE OF code SKIP LOCKED LIMIT :limit) "
                    "UPDATE public.coupon_codes AS code SET tenant_id=batch.tenant_id,updated_at=CURRENT_TIMESTAMP "
                    "FROM batch WHERE code.id=batch.id"
                ),
                {"limit": _BATCH_SIZE},
            ).rowcount
            if not updated:
                break
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("LOCK TABLE public.coupon_codes IN SHARE ROW EXCLUSIVE MODE")
    missing = op.get_bind().execute(
        sa.text(
            "SELECT code.id FROM public.coupon_codes AS code "
            "LEFT JOIN public.coupon_pools AS pool ON pool.id=code.pool_id "
            "WHERE code.tenant_id IS NULL OR pool.id IS NULL OR code.tenant_id IS DISTINCT FROM pool.tenant_id "
            "ORDER BY code.id LIMIT 1"
        )
    ).first()
    if missing is not None:
        raise RuntimeError(f"coupon-code tenant backfill is incomplete at code_id={missing[0]}")


def downgrade() -> None:
    # Derived tenant data is intentionally retained for retry-safe downgrade.
    pass
