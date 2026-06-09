"""make diversion_clues.code_item_id nullable

Revision ID: dc03575c9a6c
Revises: f82f8dc246a2
Create Date: 2026-06-09 20:29:54.229848

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'dc03575c9a6c'
down_revision: Union[str, None] = 'f82f8dc246a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('diversion_clues', 'code_item_id',
               existing_type=sa.UUID(),
               nullable=True)


def downgrade() -> None:
    op.alter_column('diversion_clues', 'code_item_id',
               existing_type=sa.UUID(),
               nullable=False)
