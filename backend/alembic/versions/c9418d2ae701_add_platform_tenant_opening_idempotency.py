"""add platform tenant opening idempotency

Revision ID: c9418d2ae701
Revises: b8307c56753e
Create Date: 2026-08-03 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9418d2ae701"
down_revision: str | None = "b8307c56753e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    # 平台全局控制面回执：仅 platform_admin 的 bypass 会话访问，不属于租户 RLS 数据面。
    op.create_table(
        "platform_tenant_openings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("initial_admin_id", sa.Uuid(), nullable=True),
        sa.Column("initial_admin_state", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["initial_admin_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("initial_admin_id"),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.drop_table("platform_tenant_openings")
