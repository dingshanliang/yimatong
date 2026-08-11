"""Backfill campaign timestamp shadows in independently committed batches.

Revision ID: u6ab1c2d3e4f
Revises: u6a1b2c3d4e5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6ab1c2d3e4f"
down_revision: str | None = "u6a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 1_000

_START_EXPR = """CASE WHEN campaign.start_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
    THEN campaign.start_at::timestamptz
    ELSE campaign.start_at::timestamp AT TIME ZONE 'Asia/Shanghai' END"""
_END_EXPR = """CASE WHEN campaign.end_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
    THEN campaign.end_at::timestamptz
    ELSE campaign.end_at::timestamp AT TIME ZONE 'Asia/Shanghai' END"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    while True:
        with op.get_context().autocommit_block():
            result = op.get_bind().execute(
                sa.text(
                    f"""
                    WITH batch AS (
                        SELECT campaign.id,{_START_EXPR} AS parsed_start,{_END_EXPR} AS parsed_end
                        FROM public.campaigns AS campaign
                        WHERE campaign.start_at_tz IS NULL OR campaign.end_at_tz IS NULL
                        ORDER BY campaign.id LIMIT :batch_size FOR UPDATE OF campaign
                    )
                    UPDATE public.campaigns AS campaign
                    SET start_at_tz=batch.parsed_start,end_at_tz=batch.parsed_end
                    FROM batch WHERE campaign.id=batch.id
                    """
                ),
                {"batch_size": _BATCH_SIZE},
            )
        if result.rowcount == 0:
            break
    inconsistent = op.get_bind().execute(
        sa.text(
            f"""
            SELECT campaign.id FROM public.campaigns AS campaign
            WHERE campaign.start_at_tz IS NULL OR campaign.end_at_tz IS NULL
               OR campaign.start_at_tz IS DISTINCT FROM {_START_EXPR}
               OR campaign.end_at_tz IS DISTINCT FROM {_END_EXPR}
               OR campaign.end_at_tz<=campaign.start_at_tz
            LIMIT 1
            """
        )
    ).first()
    if inconsistent is not None:
        raise RuntimeError("campaign timestamp backfill did not reach an authoritative fixed point")


def downgrade() -> None:
    # Shadows remain compatible until the expand revision removes them.
    return
