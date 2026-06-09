"""member FK constraints and check constraints

Revision ID: f82f8dc246a2
Revises: a39e3d5de2e3
Create Date: 2026-06-09 09:54:04.131814

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f82f8dc246a2'
down_revision: Union[str, None] = 'a39e3d5de2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 添加 FK 约束
    op.create_foreign_key(
        "fk_point_txn_consumer", "point_transactions",
        "consumer_profiles", ["consumer_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_point_redemption_consumer", "point_redemptions",
        "consumer_profiles", ["consumer_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_point_product_benefit", "point_products",
        "benefits", ["benefit_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_point_redemption_benefit", "point_redemptions",
        "benefits", ["benefit_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_point_redemption_benefit_claim", "point_redemptions",
        "benefit_claims", ["benefit_claim_id"], ["id"],
        ondelete="SET NULL",
    )

    # 添加 CheckConstraints
    op.create_check_constraint(
        "check_point_product_cost_positive", "point_products",
        "points_cost > 0",
    )
    op.create_check_constraint(
        "check_point_product_stock_nonneg", "point_products",
        "stock >= 0",
    )
    op.create_check_constraint(
        "check_point_product_limit_nonneg", "point_products",
        "per_consumer_limit >= 0",
    )
    op.create_check_constraint(
        "check_point_txn_balance_nonneg", "point_transactions",
        "balance_after >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("check_point_txn_balance_nonneg", "point_transactions", type_="check")
    op.drop_constraint("check_point_product_limit_nonneg", "point_products", type_="check")
    op.drop_constraint("check_point_product_stock_nonneg", "point_products", type_="check")
    op.drop_constraint("check_point_product_cost_positive", "point_products", type_="check")
    op.drop_constraint("fk_point_redemption_benefit_claim", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_redemption_benefit", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_product_benefit", "point_products", type_="foreignkey")
    op.drop_constraint("fk_point_redemption_consumer", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_txn_consumer", "point_transactions", type_="foreignkey")

