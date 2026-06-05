"""add created_at updated_at to brands

Revision ID: ea922e5bb7a8
Revises: 46b151904729
Create Date: 2026-06-05 18:40:37.194518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea922e5bb7a8'
down_revision: Union[str, None] = '46b151904729'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('brands', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('brands', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))


def downgrade() -> None:
    op.drop_column('brands', 'updated_at')
    op.drop_column('brands', 'created_at')
