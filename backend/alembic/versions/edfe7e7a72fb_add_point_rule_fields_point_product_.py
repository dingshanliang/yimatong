"""add_point_rule_fields_point_product_expires

Revision ID: edfe7e7a72fb
Revises: 939bb1ca7751
Create Date: 2026-05-31 11:10:28.339059

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'edfe7e7a72fb'
down_revision: Union[str, None] = '939bb1ca7751'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # point_products 表
    op.create_table('point_products',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('image_url', sa.String(length=500), nullable=True),
    sa.Column('points_cost', sa.Integer(), nullable=False),
    sa.Column('stock', sa.Integer(), nullable=False),
    sa.Column('total_claimed', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('benefit_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_point_products_tenant', 'point_products', ['tenant_id'], unique=False)

    # point_rules 新字段
    op.add_column('point_rules', sa.Column('daily_limit', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('point_rules', sa.Column('description', sa.String(length=200), nullable=True))
    op.add_column('point_rules', sa.Column('config', sa.JSON(), nullable=True))

    # point_transactions 过期字段
    op.add_column('point_transactions', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_point_txn_expires', 'point_transactions', ['expires_at'], unique=False,
                    postgresql_where=sa.text('expires_at IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('ix_point_txn_expires', table_name='point_transactions',
                  postgresql_where=sa.text('expires_at IS NOT NULL'))
    op.drop_column('point_transactions', 'expires_at')
    op.drop_column('point_rules', 'config')
    op.drop_column('point_rules', 'description')
    op.drop_column('point_rules', 'daily_limit')
    op.drop_index('ix_point_products_tenant', table_name='point_products')
    op.drop_table('point_products')
