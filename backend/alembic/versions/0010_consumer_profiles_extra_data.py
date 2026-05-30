"""add extra_data to consumer_profiles

Revision ID: f1a2b3c4d5e6
Revises: e35952a10f5a
Create Date: 2026-05-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e35952a10f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "consumer_profiles",
        sa.Column("extra_data", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consumer_profiles", "extra_data")
