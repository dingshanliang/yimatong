"""add_code_batch_production_link

Revision ID: f5a1b2c3d4e5
Revises: c4e5f6a7b8c9
Create Date: 2026-05-31 22:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a1b2c3d4e5"
down_revision: str | None = "c4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("code_batches", sa.Column("production_batch_id", sa.Uuid(), nullable=True))
    op.add_column(
        "code_batches",
        sa.Column("generation_mode", sa.String(length=20), nullable=False, server_default="item_level"),
    )
    op.create_index(
        op.f("ix_code_batches_production_batch_id"),
        "code_batches",
        ["production_batch_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_code_batches_production_batch_id_production_batches",
        "code_batches",
        "production_batches",
        ["production_batch_id"],
        ["id"],
    )
    op.alter_column("code_batches", "generation_mode", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_code_batches_production_batch_id_production_batches",
        "code_batches",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_code_batches_production_batch_id"), table_name="code_batches")
    op.drop_column("code_batches", "generation_mode")
    op.drop_column("code_batches", "production_batch_id")
