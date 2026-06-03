"""add_tenant_health_metrics

Revision ID: 447323f8bd5d
Revises: 348e8f901bb4
Create Date: 2026-06-03 09:28:25.881526

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '447323f8bd5d'
down_revision: Union[str, None] = '348e8f901bb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_health_metrics',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('tenants.id'), unique=True, nullable=False, index=True),
        sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scans_last_7d', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('scans_last_30d', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active_campaigns', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('days_until_expiry', sa.Integer(), nullable=True),
        sa.Column('health_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('health_status', sa.String(20), nullable=False, server_default='healthy'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('tenant_health_metrics')
