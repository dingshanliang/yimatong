"""add_tenant_domain_whitelabel_extensions

Revision ID: 9e93374ca54c
Revises: edfe7e7a72fb
Create Date: 2026-05-31 11:58:44.335884

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9e93374ca54c'
down_revision: Union[str, None] = 'edfe7e7a72fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tenant_domains 表
    op.create_table('tenant_domains',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('domain', sa.String(length=253), nullable=False),
    sa.Column('ssl_status', sa.String(length=20), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('cname_target', sa.String(length=253), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('domain')
    )
    op.create_index('ix_tenant_domains_tenant', 'tenant_domains', ['tenant_id'], unique=False)

    # whitelabel_configs 新字段
    op.add_column('whitelabel_configs', sa.Column('favicon_url', sa.String(length=500), nullable=True))
    op.add_column('whitelabel_configs', sa.Column('login_bg_url', sa.String(length=500), nullable=True))
    op.add_column('whitelabel_configs', sa.Column('font_family', sa.String(length=100), nullable=False, server_default=''))
    op.add_column('whitelabel_configs', sa.Column('custom_css', sa.String(length=5000), nullable=True))


def downgrade() -> None:
    op.drop_column('whitelabel_configs', 'custom_css')
    op.drop_column('whitelabel_configs', 'font_family')
    op.drop_column('whitelabel_configs', 'login_bg_url')
    op.drop_column('whitelabel_configs', 'favicon_url')
    op.drop_index('ix_tenant_domains_tenant', table_name='tenant_domains')
    op.drop_table('tenant_domains')
