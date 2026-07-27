"""add diversion clue explainability fields

Revision ID: m4b5c6d7e8f9
Revises: l3a4b5c6d7e8
Create Date: 2026-07-27 23:00:00.000000

yimatong-zgb1.16：DiversionClue 加可解释性字段（rule_name/confidence/pending_review/observation_count）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m4b5c6d7e8f9"
down_revision: str | None = "l3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diversion_clues",
        sa.Column("rule_name", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("confidence", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("pending_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("diversion_clues", "observation_count")
    op.drop_column("diversion_clues", "pending_review")
    op.drop_column("diversion_clues", "confidence")
    op.drop_column("diversion_clues", "rule_name")
