"""add tenant categories column

Revision ID: 46b151904729
Revises: 8ed6e2861a45
Create Date: 2026-06-04 00:10:18.576029

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46b151904729'
down_revision: Union[str, None] = '8ed6e2861a45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('categories', sa.JSON(), nullable=True, comment='租户品类配置'))


def downgrade() -> None:
    op.drop_column('tenants', 'categories')
