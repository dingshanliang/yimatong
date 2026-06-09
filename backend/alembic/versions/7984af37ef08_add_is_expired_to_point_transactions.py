"""add is_expired to point_transactions

Revision ID: 7984af37ef08
Revises: 6984e9ecb795
Create Date: 2026-06-09 09:52:58.838394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7984af37ef08'
down_revision: Union[str, None] = '6984e9ecb795'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "point_transactions",
        sa.Column("is_expired", sa.Boolean(), nullable=False, server_default="false"),
    )
    # 将已清零的记录标记为 is_expired（保留 amount 用于审计）
    op.execute(
        "UPDATE point_transactions SET is_expired = true WHERE amount = 0 AND txn_type = 'earning'"
    )


def downgrade() -> None:
    op.drop_column("point_transactions", "is_expired")
