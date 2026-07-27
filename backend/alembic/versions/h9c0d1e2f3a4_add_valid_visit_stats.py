"""add valid visit stats columns

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-07-27 18:00:00.000000

yimatong-zgb1.10：DailyScanStats 加 valid_visits / valid_uv（Decision 21 headline 分母）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h9c0d1e2f3a4"
down_revision: str | None = "g8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_scan_stats",
        sa.Column("valid_visits", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "daily_scan_stats",
        sa.Column("valid_uv", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("daily_scan_stats", "valid_uv")
    op.drop_column("daily_scan_stats", "valid_visits")
