"""add created_at to platform_audit_log and updated_at to point_transactions

Revision ID: 6610c6bbbddd
Revises: 49a714248b9e
Create Date: 2026-06-05 20:54:13.288435

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6610c6bbbddd'
down_revision: Union[str, None] = '49a714248b9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('platform_audit_log', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('point_transactions', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))


def downgrade() -> None:
    op.drop_column('point_transactions', 'updated_at')
    op.drop_column('platform_audit_log', 'created_at')
