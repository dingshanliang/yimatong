"""add consumed refresh tokens

Revision ID: d052bb312c44
Revises: c9418d2ae701
Create Date: 2026-08-03 16:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d052bb312c44"
down_revision: str | None = "c9418d2ae701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    # 全局认证控制面表；登录/刷新只通过受控 auth bypass 会话访问。
    op.create_table(
        "consumed_refresh_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        op.f("ix_consumed_refresh_tokens_expires_at"),
        "consumed_refresh_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.drop_index(op.f("ix_consumed_refresh_tokens_expires_at"), table_name="consumed_refresh_tokens")
    op.drop_table("consumed_refresh_tokens")
