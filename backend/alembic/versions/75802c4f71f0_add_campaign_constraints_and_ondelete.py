"""add_campaign_constraints_and_ondelete

Revision ID: 75802c4f71f0
Revises: 723345d2b4c1
Create Date: 2026-06-07 20:26:23.016103

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '75802c4f71f0'
down_revision: Union[str, None] = '723345d2b4c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- campaigns: 添加租户内名称唯一约束 ---
    op.create_unique_constraint('uq_campaign_tenant_name', 'campaigns', ['tenant_id', 'name'])

    # --- benefits: 添加 CheckConstraint ---
    op.execute(
        "ALTER TABLE benefits ADD CONSTRAINT check_benefit_stock_not_exceeded "
        "CHECK (stock_used <= stock_total)"
    )
    op.execute(
        "ALTER TABLE benefits ADD CONSTRAINT check_benefit_stock_total_non_negative "
        "CHECK (stock_total >= 0)"
    )
    op.execute(
        "ALTER TABLE benefits ADD CONSTRAINT check_benefit_per_person_limit_min "
        "CHECK (per_person_limit >= 1)"
    )

    # --- benefits: 外键 ondelete 策略 ---
    op.drop_constraint('benefits_campaign_id_fkey', 'benefits', type_='foreignkey')
    op.create_foreign_key(
        'benefits_campaign_id_fkey', 'benefits', 'campaigns',
        ['campaign_id'], ['id'], ondelete='SET NULL',
    )
    op.drop_constraint('fk_benefits_connector_id', 'benefits', type_='foreignkey')
    op.create_foreign_key(
        'fk_benefits_connector_id', 'benefits', 'connectors',
        ['connector_id'], ['id'], ondelete='SET NULL',
    )

    # --- benefit_claims: 外键 ondelete 策略 ---
    op.drop_constraint('benefit_claims_benefit_id_fkey', 'benefit_claims', type_='foreignkey')
    op.create_foreign_key(
        'benefit_claims_benefit_id_fkey', 'benefit_claims', 'benefits',
        ['benefit_id'], ['id'], ondelete='RESTRICT',
    )
    op.drop_constraint('benefit_claims_campaign_id_fkey', 'benefit_claims', type_='foreignkey')
    op.create_foreign_key(
        'benefit_claims_campaign_id_fkey', 'benefit_claims', 'campaigns',
        ['campaign_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    # --- benefit_claims: 恢复原始外键 ---
    op.drop_constraint('benefit_claims_campaign_id_fkey', 'benefit_claims', type_='foreignkey')
    op.create_foreign_key(
        'benefit_claims_campaign_id_fkey', 'benefit_claims', 'campaigns',
        ['campaign_id'], ['id'],
    )
    op.drop_constraint('benefit_claims_benefit_id_fkey', 'benefit_claims', type_='foreignkey')
    op.create_foreign_key(
        'benefit_claims_benefit_id_fkey', 'benefit_claims', 'benefits',
        ['benefit_id'], ['id'],
    )

    # --- benefits: 恢复原始外键 ---
    op.drop_constraint('fk_benefits_connector_id', 'benefits', type_='foreignkey')
    op.create_foreign_key(
        'fk_benefits_connector_id', 'benefits', 'connectors',
        ['connector_id'], ['id'],
    )
    op.drop_constraint('benefits_campaign_id_fkey', 'benefits', type_='foreignkey')
    op.create_foreign_key(
        'benefits_campaign_id_fkey', 'benefits', 'campaigns',
        ['campaign_id'], ['id'],
    )

    # --- benefits: 删除 CheckConstraint ---
    op.execute("ALTER TABLE benefits DROP CONSTRAINT IF EXISTS check_benefit_per_person_limit_min")
    op.execute("ALTER TABLE benefits DROP CONSTRAINT IF EXISTS check_benefit_stock_total_non_negative")
    op.execute("ALTER TABLE benefits DROP CONSTRAINT IF EXISTS check_benefit_stock_not_exceeded")

    # --- campaigns: 删除唯一约束 ---
    op.drop_constraint('uq_campaign_tenant_name', 'campaigns', type_='unique')
