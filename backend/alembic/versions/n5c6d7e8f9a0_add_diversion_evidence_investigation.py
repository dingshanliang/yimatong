"""add diversion evidence table and investigation fields

Revision ID: n5c6d7e8f9a0
Revises: m4b5c6d7e8f9
Create Date: 2026-07-28 00:00:00.000000

yimatong-zgb1.17：防窜调查证据链 + 调查协作字段。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n5c6d7e8f9a0"
down_revision: str | None = "m4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DiversionClue 加调查协作字段
    op.add_column(
        "diversion_clues",
        sa.Column("investigation_status", sa.String(length=30), nullable=False, server_default="open"),
    )
    op.add_column(
        "diversion_clues",
        sa.Column("assigned_to", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_diversion_clues_assigned_to", "diversion_clues", ["assigned_to"])

    # DiversionEvidence 新表（AC2 证据链）
    op.create_table(
        "diversion_evidence",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clue_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("file_url", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=100), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clue_id"], ["diversion_clues.id"]),
    )
    op.create_index("ix_diversion_evidence_tenant_id", "diversion_evidence", ["tenant_id"])
    op.create_index("ix_diversion_evidence_clue_id", "diversion_evidence", ["clue_id"])


def downgrade() -> None:
    op.drop_index("ix_diversion_evidence_clue_id", table_name="diversion_evidence")
    op.drop_index("ix_diversion_evidence_tenant_id", table_name="diversion_evidence")
    op.drop_table("diversion_evidence")
    op.drop_index("ix_diversion_clues_assigned_to", table_name="diversion_clues")
    op.drop_column("diversion_clues", "assigned_to")
    op.drop_column("diversion_clues", "investigation_status")
