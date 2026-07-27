"""add flow versioning and location observation fields

Revision ID: l3a4b5c6d7e8
Revises: k2f3a4b5c6d7
Create Date: 2026-07-27 22:00:00.000000

yimatong-zgb1.15：版本化渠道流向（Decision 53）+ 扫码位置观察（Decision 54）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l3a4b5c6d7e8"
down_revision: str | None = "k2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CodeAllocation 版本化字段（Decision 53）
    op.add_column(
        "code_allocations",
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "code_allocations",
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "code_allocations",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "code_allocations",
        sa.Column("change_reason", sa.String(length=200), nullable=True),
    )
    op.create_index(
        "ix_code_alloc_batch_effective",
        "code_allocations",
        ["batch_id", "effective_from", "effective_to"],
    )

    # scan_events 位置观察字段（Decision 54）
    op.add_column(
        "scan_events",
        sa.Column("location_source", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "scan_events",
        sa.Column("location_accuracy", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "scan_events",
        sa.Column("location_authorized", sa.Boolean(), nullable=True),
    )

    # DiversionClue 位置来源字段（Decision 54）
    op.add_column(
        "diversion_clues",
        sa.Column("location_source", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("location_accuracy", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("location_authorized", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("diversion_clues", "location_authorized")
    op.drop_column("diversion_clues", "location_accuracy")
    op.drop_column("diversion_clues", "location_source")
    op.drop_column("scan_events", "location_authorized")
    op.drop_column("scan_events", "location_accuracy")
    op.drop_column("scan_events", "location_source")
    op.drop_index("ix_code_alloc_batch_effective", table_name="code_allocations")
    op.drop_column("code_allocations", "change_reason")
    op.drop_column("code_allocations", "version")
    op.drop_column("code_allocations", "effective_to")
    op.drop_column("code_allocations", "effective_from")
