"""add ai_generations table

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_generations_tenant_id", "ai_generations", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_generations_tenant_id", table_name="ai_generations")
    op.drop_table("ai_generations")
