"""member model indexes and constraints

Revision ID: a39e3d5de2e3
Revises: 7984af37ef08
Create Date: 2026-06-09 09:53:28.512206

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a39e3d5de2e3'
down_revision: Union[str, None] = '7984af37ef08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. ConsumerProfile: 添加 (tenant_id, phone_hash) 复合索引
    op.create_index(
        "ix_consumer_profiles_tenant_phone", "consumer_profiles",
        ["tenant_id", "phone_hash"],
    )
    # 添加唯一约束防止重复手机号消费者
    op.create_unique_constraint(
        "uq_consumer_tenant_phone", "consumer_profiles",
        ["tenant_id", "phone_hash"],
    )
    # 移除重复的单列索引（ix_consumer_profiles_tenant 与 tenant_id index=True 重复）
    op.drop_index("ix_consumer_profiles_tenant", table_name="consumer_profiles")

    # 2. PointProduct: 添加复合索引（如果已存在则跳过）
    op.create_index(
        "ix_point_products_tenant_enabled_sort", "point_products",
        ["tenant_id", "enabled", "sort_order"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_point_products_tenant_enabled_sort", table_name="point_products")
    op.create_index("ix_consumer_profiles_tenant", "consumer_profiles", ["tenant_id"])
    op.drop_constraint("uq_consumer_tenant_phone", "consumer_profiles", type_="unique")
    op.drop_index("ix_consumer_profiles_tenant_phone", table_name="consumer_profiles")
