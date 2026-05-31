"""enhance gmv attribution and add daily stats

Revision ID: 730251dcc816
Revises: d7ccce4445e0
Create Date: 2026-05-31 10:17:34.564957

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '730251dcc816'
down_revision: Union[str, None] = 'd7ccce4445e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- external_orders: add channel, source_system ---
    op.add_column('external_orders', sa.Column('channel', sa.String(length=50), nullable=True))
    op.add_column('external_orders', sa.Column('source_system', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_external_orders_channel'), 'external_orders', ['channel'], unique=False)

    # --- gmv_attributions: add attribution detail fields ---
    op.add_column('gmv_attributions', sa.Column('code_item_id', sa.Uuid(), nullable=True))
    op.add_column('gmv_attributions', sa.Column('campaign_id', sa.Uuid(), nullable=True))
    op.add_column('gmv_attributions', sa.Column('consumer_id', sa.Uuid(), nullable=True))
    op.add_column('gmv_attributions', sa.Column('scan_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column('gmv_attributions', sa.Column(
        'attribution_window_hours', sa.Integer(), nullable=False, server_default='168'))
    op.add_column('gmv_attributions', sa.Column(
        'confidence_score', sa.Float(), nullable=False, server_default='1.0'))
    op.create_index('ix_gmv_attr_tenant_campaign', 'gmv_attributions', ['tenant_id', 'campaign_id'], unique=False)
    op.create_index(op.f('ix_gmv_attributions_campaign_id'), 'gmv_attributions', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_gmv_attributions_code_item_id'), 'gmv_attributions', ['code_item_id'], unique=False)
    op.create_index(op.f('ix_gmv_attributions_consumer_id'), 'gmv_attributions', ['consumer_id'], unique=False)

    # --- gmv_daily_stats: new table ---
    op.create_table('gmv_daily_stats',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('stat_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('campaign_id', sa.Uuid(), nullable=True),
        sa.Column('channel', sa.String(length=50), nullable=True),
        sa.Column('attributed_gmv', sa.Float(), nullable=False, server_default='0'),
        sa.Column('attributed_orders', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('scan_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('scan_uv', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_gmv_daily_stats_tenant_id'), 'gmv_daily_stats', ['tenant_id'], unique=False)
    op.create_index('ix_gmv_daily_tenant_date', 'gmv_daily_stats', ['tenant_id', 'stat_date'], unique=False)
    op.create_index(op.f('ix_gmv_daily_stats_campaign_id'), 'gmv_daily_stats', ['campaign_id'], unique=False)
    op.create_index('uq_gmv_daily_stats', 'gmv_daily_stats',
                     ['tenant_id', 'stat_date', 'campaign_id', 'channel'], unique=True)


def downgrade() -> None:
    # --- gmv_daily_stats ---
    op.drop_index('uq_gmv_daily_stats', table_name='gmv_daily_stats')
    op.drop_index(op.f('ix_gmv_daily_stats_campaign_id'), table_name='gmv_daily_stats')
    op.drop_index('ix_gmv_daily_tenant_date', table_name='gmv_daily_stats')
    op.drop_index(op.f('ix_gmv_daily_stats_tenant_id'), table_name='gmv_daily_stats')
    op.drop_table('gmv_daily_stats')

    # --- gmv_attributions ---
    op.drop_index(op.f('ix_gmv_attributions_consumer_id'), table_name='gmv_attributions')
    op.drop_index(op.f('ix_gmv_attributions_code_item_id'), table_name='gmv_attributions')
    op.drop_index(op.f('ix_gmv_attributions_campaign_id'), table_name='gmv_attributions')
    op.drop_index('ix_gmv_attr_tenant_campaign', table_name='gmv_attributions')
    op.drop_column('gmv_attributions', 'confidence_score')
    op.drop_column('gmv_attributions', 'attribution_window_hours')
    op.drop_column('gmv_attributions', 'scan_time')
    op.drop_column('gmv_attributions', 'consumer_id')
    op.drop_column('gmv_attributions', 'campaign_id')
    op.drop_column('gmv_attributions', 'code_item_id')

    # --- external_orders ---
    op.drop_index(op.f('ix_external_orders_channel'), table_name='external_orders')
    op.drop_column('external_orders', 'source_system')
    op.drop_column('external_orders', 'channel')
