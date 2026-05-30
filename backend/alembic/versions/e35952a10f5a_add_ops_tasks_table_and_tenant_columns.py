"""add ops_tasks table and tenant columns

Revision ID: e35952a10f5a
Revises: 9965e0679a13
Create Date: 2026-05-30 08:25:39.875514

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e35952a10f5a'
down_revision: Union[str, None] = '9965e0679a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ops_tasks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('status', sa.Enum('pending', 'in_progress', 'completed', 'cancelled', name='opstaskstatus'), nullable=False),
    sa.Column('priority', sa.Enum('low', 'medium', 'high', name='opstaskpriority'), nullable=False),
    sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ops_tasks_tenant_id'), 'ops_tasks', ['tenant_id'], unique=False)
    op.add_column('tenants', sa.Column('plan_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenants', sa.Column('onboarding_progress', sa.JSON(), nullable=True))
    op.add_column('tenants', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_column('tenants', 'created_at')
    op.drop_column('tenants', 'onboarding_progress')
    op.drop_column('tenants', 'plan_expires_at')
    op.drop_index(op.f('ix_ops_tasks_tenant_id'), table_name='ops_tasks')
    op.drop_table('ops_tasks')
