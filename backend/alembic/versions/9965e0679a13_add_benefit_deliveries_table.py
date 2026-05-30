"""add_benefit_deliveries_table

Revision ID: 9965e0679a13
Revises: 0009
Create Date: 2026-05-30 08:12:40.443334

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9965e0679a13'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('benefit_deliveries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('connector_id', sa.Uuid(), nullable=False),
    sa.Column('consumer_id', sa.String(length=100), nullable=False),
    sa.Column('benefit_type', sa.String(length=50), nullable=False),
    sa.Column('benefit_config', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('external_data', sa.JSON(), nullable=True),
    sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_benefit_deliveries_connector_id'), 'benefit_deliveries', ['connector_id'], unique=False)
    op.create_index('ix_benefit_deliveries_retry', 'benefit_deliveries', ['status', 'next_retry_at'], unique=False)
    op.create_index(op.f('ix_benefit_deliveries_tenant_id'), 'benefit_deliveries', ['tenant_id'], unique=False)
    op.create_index('ix_benefit_deliveries_tenant_status', 'benefit_deliveries', ['tenant_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_benefit_deliveries_tenant_status', table_name='benefit_deliveries')
    op.drop_index(op.f('ix_benefit_deliveries_tenant_id'), table_name='benefit_deliveries')
    op.drop_index('ix_benefit_deliveries_retry', table_name='benefit_deliveries')
    op.drop_index(op.f('ix_benefit_deliveries_connector_id'), table_name='benefit_deliveries')
    op.drop_table('benefit_deliveries')
