"""Backfill legacy frozen provenance in independently committed batches.

Revision ID: af64b8d2e5a7
Revises: 9e53a7c1d4f6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "af64b8d2e5a7"
down_revision: str | None = "9e53a7c1d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 1_000


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    while True:
        with op.get_context().autocommit_block():
            result = op.get_bind().execute(
                sa.text(
                    """
                    WITH batch AS (
                        SELECT id FROM public.code_items
                        WHERE status::text='frozen' AND freeze_provenance_version IS NULL
                        ORDER BY id LIMIT :batch_size FOR UPDATE
                    )
                    UPDATE public.code_items item
                    SET frozen_from_status=CASE WHEN item.bound_at IS NOT NULL THEN 'bound' ELSE 'activated' END,
                        frozen_at=COALESCE(item.updated_at,item.created_at,CURRENT_TIMESTAMP),
                        freeze_provenance_version=0
                    FROM batch WHERE item.id=batch.id
                    """
                ),
                {"batch_size": _BATCH_SIZE},
            )
        if result.rowcount == 0:
            break


def downgrade() -> None:
    # Version-0 provenance is benign compatibility enrichment. It is retained
    # until the expand revision removes the nullable columns, avoiding another
    # high-volume rewrite during an intermediate rollback.
    return
