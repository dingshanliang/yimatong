"""epic23_add_enabled_features_wechat_openid_cash_red_packet

Revision ID: 6c2dbc9c4bbf
Revises: fd41060e5f42
Create Date: 2026-05-30 13:05:26.561995

EPIC-23: 现金红包插件
- tenants 加 enabled_features JSON 列
- tenants 加 onboarding_progress JSON 列
- tenants 加 plan_expires_at 列
- tenants 加 created_at 列（NOT NULL, server_default）
- consumer_profiles 加 wechat_openid 列
- consumer_profiles 加 extra_data 列
- consumer_profiles 唯一约束从 phone_hash 改为 tenant_id + wechat_openid
- 删除废弃的 redpacket_rules, redpacket_claims, kyc_records, withdrawals 表
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6c2dbc9c4bbf'
down_revision: str | None = 'fd41060e5f42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    """Check if a column already exists in a table (PostgreSQL)."""
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    if result is None:
        # Offline SQL generation cannot inspect information_schema. These
        # checks only guard idempotent columns, so assume the prior migration
        # chain already owns them and keep --sql generation deterministic.
        return True
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # --- tenants 新增列 ---
    op.add_column('tenants', sa.Column('enabled_features', sa.JSON(), nullable=True))

    # onboarding_progress, plan_expires_at, created_at may already exist
    # from migration e35952a10f5a (add_ops_tasks_table_and_tenant_columns)
    if not _column_exists(conn, 'tenants', 'onboarding_progress'):
        op.add_column('tenants', sa.Column('onboarding_progress', sa.JSON(), nullable=True))
    if not _column_exists(conn, 'tenants', 'plan_expires_at'):
        op.add_column('tenants', sa.Column('plan_expires_at', sa.DateTime(timezone=True), nullable=True))
    if not _column_exists(conn, 'tenants', 'created_at'):
        op.add_column('tenants', sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ))

    # --- consumer_profiles 新增 wechat_openid ---
    op.add_column('consumer_profiles', sa.Column('wechat_openid', sa.String(length=128), nullable=True))

    # extra_data may already exist from migration f1a2b3c4d5e6 (0010_consumer_profiles_extra_data)
    if not _column_exists(conn, 'consumer_profiles', 'extra_data'):
        op.add_column('consumer_profiles', sa.Column('extra_data', sa.JSON(), nullable=True))

    # --- consumer_profiles 唯一约束迁移 ---
    op.drop_constraint('consumer_profiles_phone_hash_key', 'consumer_profiles', type_='unique')
    op.create_unique_constraint('uq_consumer_tenant_openid', 'consumer_profiles', ['tenant_id', 'wechat_openid'])

    # --- 删除废弃红包骨架表 ---
    op.drop_table('withdrawals')
    op.drop_table('kyc_records')
    op.drop_table('redpacket_claims')
    op.drop_table('redpacket_rules')


def downgrade() -> None:
    # --- 恢复废弃红包骨架表 ---
    op.create_table('redpacket_rules',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('name', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.Column('total_budget', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('min_amount', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('max_amount', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('daily_limit_per_user', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('single_limit_per_user', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('require_kyc', sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), autoincrement=False, nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), autoincrement=False, nullable=False),
        sa.Column('status', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column('claimed_budget', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name='redpacket_rules_pkey'),
    )
    op.create_index('ix_redpacket_rules_tenant_id', 'redpacket_rules', ['tenant_id'], unique=False)

    op.create_table('redpacket_claims',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('rule_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('account_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('amount', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('status', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name='redpacket_claims_pkey'),
    )
    op.create_index('ix_redpacket_claims_rule_account', 'redpacket_claims', ['rule_id', 'account_id'], unique=False)

    op.create_table('kyc_records',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('account_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('real_name', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column('id_number', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column('phone_encrypted', sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column('phone_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column('status', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name='kyc_records_pkey'),
    )
    op.create_index('ix_kyc_tenant_account', 'kyc_records', ['tenant_id', 'account_id'], unique=False)

    op.create_table('withdrawals',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('account_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('amount', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('status', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name='withdrawals_pkey'),
    )
    op.create_index('ix_withdrawals_tenant_account', 'withdrawals', ['tenant_id', 'account_id'], unique=False)

    # --- consumer_profiles 回滚 ---
    op.drop_constraint('uq_consumer_tenant_openid', 'consumer_profiles', type_='unique')
    op.create_unique_constraint('consumer_profiles_phone_hash_key', 'consumer_profiles', ['phone_hash'])
    # Only drop extra_data if THIS migration added it (not the earlier 0010 migration)
    conn = op.get_bind()
    if _column_exists(conn, 'consumer_profiles', 'wechat_openid'):
        op.drop_column('consumer_profiles', 'wechat_openid')
    # Note: extra_data is owned by migration f1a2b3c4d5e6 (0010), not this one.
    # onboarding_progress, plan_expires_at, created_at are owned by e35952a10f5a, not this one.

    # --- tenants 回滚 ---
    # Only drop enabled_features (owned by this migration).
    # onboarding_progress, plan_expires_at, created_at are owned by e35952a10f5a.
    if _column_exists(conn, 'tenants', 'enabled_features'):
        op.drop_column('tenants', 'enabled_features')
