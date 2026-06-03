"""add_plan_definitions_table

Revision ID: 348e8f901bb4
Revises: a3b4c5d6e7f8
Create Date: 2026-06-03 09:19:16.893870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '348e8f901bb4'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan_definitions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(50), unique=True, nullable=False),
        sa.Column('display_name', sa.String(100), nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('price_yearly', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('quota_defaults', sa.JSON(), nullable=True),
        sa.Column('feature_flags', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Seed default plans
    op.execute("""
        INSERT INTO plan_definitions (id, name, display_name, description, price_yearly, quota_defaults, feature_flags, sort_order)
        VALUES
            ('plan-free', 'free', '免费版', '基础功能，适合试用', 0,
             '{"max_codes": 1000, "max_scans": 5000, "max_campaigns": 3, "max_accounts": 2}'::jsonb,
             '{"ai_assistant": false, "risk_module": false, "channel_portal": false}'::jsonb, 0),
            ('plan-starter', 'starter', '入门版', '适合小型品牌', 29900,
             '{"max_codes": 10000, "max_scans": 50000, "max_campaigns": 10, "max_accounts": 5}'::jsonb,
             '{"ai_assistant": true, "risk_module": false, "channel_portal": false}'::jsonb, 1),
            ('plan-pro', 'pro', '专业版', '适合成长型品牌', 99900,
             '{"max_codes": 100000, "max_scans": 500000, "max_campaigns": 50, "max_accounts": 20}'::jsonb,
             '{"ai_assistant": true, "risk_module": true, "channel_portal": true}'::jsonb, 2),
            ('plan-enterprise', 'enterprise', '企业版', '无限制，专属服务', 299900,
             '{"max_codes": -1, "max_scans": -1, "max_campaigns": -1, "max_accounts": -1}'::jsonb,
             '{"ai_assistant": true, "risk_module": true, "channel_portal": true, "white_label": true}'::jsonb, 3)
        ON CONFLICT (name) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('plan_definitions')
