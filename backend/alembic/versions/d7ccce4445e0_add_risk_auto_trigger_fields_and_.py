"""add_risk_auto_trigger_fields_and_notifications

Revision ID: d7ccce4445e0
Revises: 0011
Create Date: 2026-05-31 10:01:42.027805

EPIC-13: 风控规则自动触发与活动自动停用闭环
- interception_records 新增 auto_triggered, action_taken, action_detail 字段
- 新建 risk_notifications 表（Admin 风控通知）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd7ccce4445e0'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- risk_notifications 新表 ---
    op.create_table('risk_notifications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('notification_type', sa.String(length=30), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('detail', sa.String(length=500), nullable=False),
        sa.Column('risk_rule_id', sa.Uuid(), nullable=True),
        sa.Column('campaign_id', sa.Uuid(), nullable=True),
        sa.Column('code_item_id', sa.Uuid(), nullable=True),
        sa.Column('read', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_risk_notifications_tenant_id'), 'risk_notifications', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_risk_notifications_notification_type'), 'risk_notifications', ['notification_type'], unique=False)
    op.create_index(op.f('ix_risk_notifications_risk_rule_id'), 'risk_notifications', ['risk_rule_id'], unique=False)
    op.create_index(op.f('ix_risk_notifications_campaign_id'), 'risk_notifications', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_risk_notifications_code_item_id'), 'risk_notifications', ['code_item_id'], unique=False)
    op.create_index('ix_risk_notif_tenant_read', 'risk_notifications', ['tenant_id', 'read'], unique=False)

    # --- interception_records 新增字段 ---
    op.add_column('interception_records', sa.Column('auto_triggered', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('interception_records', sa.Column('action_taken', sa.String(length=50), nullable=True))
    op.add_column('interception_records', sa.Column('action_detail', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('interception_records', 'action_detail')
    op.drop_column('interception_records', 'action_taken')
    op.drop_column('interception_records', 'auto_triggered')

    op.drop_index('ix_risk_notif_tenant_read', table_name='risk_notifications')
    op.drop_index(op.f('ix_risk_notifications_code_item_id'), table_name='risk_notifications')
    op.drop_index(op.f('ix_risk_notifications_campaign_id'), table_name='risk_notifications')
    op.drop_index(op.f('ix_risk_notifications_risk_rule_id'), table_name='risk_notifications')
    op.drop_index(op.f('ix_risk_notifications_notification_type'), table_name='risk_notifications')
    op.drop_index(op.f('ix_risk_notifications_tenant_id'), table_name='risk_notifications')
    op.drop_table('risk_notifications')
