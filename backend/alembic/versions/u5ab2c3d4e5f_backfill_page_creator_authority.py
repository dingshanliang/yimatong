"""Backfill page creator tenant identity in independently committed batches.

Revision ID: u5ab2c3d4e5f
Revises: u5a1b2c3d4e5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5ab2c3d4e5f"
down_revision: str | None = "u5a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 1_000


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    invalid_creator = op.get_bind().execute(
        sa.text(
            """
            SELECT version.id FROM public.page_versions AS version
            LEFT JOIN public.accounts AS account ON account.id=version.created_by
            WHERE version.created_by_tenant_id IS NULL
            GROUP BY version.id HAVING count(account.id)<>1 LIMIT 1
            """
        )
    ).first()
    if invalid_creator is not None:
        raise RuntimeError("page version creator backfill requires exactly one authoritative account")
    while True:
        with op.get_context().autocommit_block():
            result = op.get_bind().execute(
                sa.text(
                    """
                    WITH batch AS (
                        SELECT version.id,account.tenant_id AS creator_tenant_id
                        FROM public.page_versions AS version
                        JOIN public.accounts AS account ON account.id=version.created_by
                        WHERE version.created_by_tenant_id IS NULL
                        ORDER BY version.id LIMIT :batch_size FOR UPDATE OF version
                    )
                    UPDATE public.page_versions AS version
                    SET created_by_tenant_id=batch.creator_tenant_id
                    FROM batch WHERE version.id=batch.id
                    """
                ),
                {"batch_size": _BATCH_SIZE},
            )
        if result.rowcount == 0:
            break
    inconsistent_creator = op.get_bind().execute(
        sa.text(
            """
            SELECT version.id FROM public.page_versions AS version
            LEFT JOIN public.accounts AS account
              ON account.id=version.created_by AND account.tenant_id=version.created_by_tenant_id
            WHERE version.created_by_tenant_id IS NULL OR account.id IS NULL LIMIT 1
            """
        )
    ).first()
    if inconsistent_creator is not None:
        raise RuntimeError("page version creator backfill did not reach an authoritative fixed point")


def downgrade() -> None:
    # Compatibility enrichment remains until the expand revision removes the
    # column, avoiding an unnecessary high-volume rewrite on intermediate rollback.
    return
