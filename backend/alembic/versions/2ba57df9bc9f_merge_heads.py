"""merge heads

Revision ID: 2ba57df9bc9f
Revises: 4d5e6f7a8091, a1b2c3d4e5f6
Create Date: 2026-06-02 22:59:04.251547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ba57df9bc9f'
down_revision: Union[str, None] = ('4d5e6f7a8091', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
