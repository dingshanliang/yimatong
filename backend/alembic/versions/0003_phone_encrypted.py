"""add phone_encrypted to consumer_profiles

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002_enable_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consumer_profiles",
        sa.Column("phone_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consumer_profiles", "phone_encrypted")
