"""add order status, refund_amount, currency, dedup constraint

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-07-27 20:00:00.000000

yimatong-zgb1.13：订单状态 + 退款净额 + 去重约束（Decision 30）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j1e2f3a4b5c6"
down_revision: str | None = "i0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 订单状态 + 退款金额 + 币种
    op.add_column(
        "external_orders",
        sa.Column("status", sa.String(length=30), nullable=False, server_default="paid"),
    )
    op.add_column(
        "external_orders",
        sa.Column("refund_amount", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "external_orders",
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"),
    )
    # 去重 unique constraint（同 source + external_id 只导入一次）
    # 注意：存量数据可能有 NULL source_system，用 source_system COALESCE 处理
    op.execute(
        "UPDATE external_orders SET source_system = 'unknown' WHERE source_system IS NULL"
    )
    op.create_unique_constraint(
        "uq_ext_orders_source_external",
        "external_orders",
        ["tenant_id", "source_system", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ext_orders_source_external", "external_orders", type_="unique")
    op.drop_column("external_orders", "currency")
    op.drop_column("external_orders", "refund_amount")
    op.drop_column("external_orders", "status")
