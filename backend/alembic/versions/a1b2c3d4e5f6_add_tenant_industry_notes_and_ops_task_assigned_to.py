"""add tenant industry notes and ops task assigned_to

Revision ID: a1b2c3d4e5f6
Revises: fd41060e5f42
Create Date: 2026-06-02 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'fd41060e5f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated ###
    op.add_column('tenants', sa.Column('industry', sa.String(length=50), nullable=True))
    op.add_column('tenants', sa.Column('notes', sa.String(length=1000), nullable=True))
    op.add_column('ops_tasks', sa.Column('assigned_to', sa.UUID(), nullable=True))
    op.create_index('ix_ops_tasks_assigned_to', 'ops_tasks', ['assigned_to'], unique=False)
    op.create_foreign_key('fk_ops_tasks_assigned_to_accounts', 'ops_tasks', 'accounts', ['assigned_to'], ['id'])
    # ### end commands auto generated ###


def downgrade() -> None:
    # ### commands auto generated ###
    op.drop_constraint('fk_ops_tasks_assigned_to_accounts', 'ops_tasks', type_='foreignkey')
    op.drop_index('ix_ops_tasks_assigned_to', table_name='ops_tasks')
    op.drop_column('ops_tasks', 'assigned_to')
    op.drop_column('tenants', 'notes')
    op.drop_column('tenants', 'industry')
    # ### end commands auto generated ###
