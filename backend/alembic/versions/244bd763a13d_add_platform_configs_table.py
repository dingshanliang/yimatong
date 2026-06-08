"""add platform_configs table

Revision ID: 244bd763a13d
Revises: 75802c4f71f0
Create Date: 2026-06-07 21:43:33.038476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '244bd763a13d'
down_revision: Union[str, None] = '75802c4f71f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('platform_configs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('platform_configs')
