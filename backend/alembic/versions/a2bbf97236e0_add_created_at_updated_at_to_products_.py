"""add created_at updated_at to products code_batches campaigns production_batches

Revision ID: a2bbf97236e0
Revises: ea922e5bb7a8
Create Date: 2026-06-05 19:25:18.633990

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2bbf97236e0'
down_revision: Union[str, None] = 'ea922e5bb7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('campaigns', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('campaigns', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('code_batches', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('code_batches', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('production_batches', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('production_batches', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('products', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('products', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'updated_at')
    op.drop_column('products', 'created_at')
    op.drop_column('production_batches', 'updated_at')
    op.drop_column('production_batches', 'created_at')
    op.drop_column('code_batches', 'updated_at')
    op.drop_column('code_batches', 'created_at')
    op.drop_column('campaigns', 'updated_at')
    op.drop_column('campaigns', 'created_at')
