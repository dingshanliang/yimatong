"""add diversion investigation history

Revision ID: o6d7e8f9a0b1
Revises: n5c6d7e8f9a0
Create Date: 2026-07-28 01:00:00.000000

yimatong-zgb1.18 AC3：调查状态变更历史（操作人/时间/前后值/原因）。
支持重开（AC2）：旧结论保留在历史中。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o6d7e8f9a0b1"
down_revision: str | None = "n5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diversion_investigation_history",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clue_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clue_id"], ["diversion_clues.id"]),
    )
    op.create_index(
        "ix_diversion_history_tenant_id", "diversion_investigation_history", ["tenant_id"]
    )
    op.create_index(
        "ix_diversion_history_clue_id", "diversion_investigation_history", ["clue_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_diversion_history_clue_id", table_name="diversion_investigation_history")
    op.drop_index("ix_diversion_history_tenant_id", table_name="diversion_investigation_history")
    op.drop_table("diversion_investigation_history")
