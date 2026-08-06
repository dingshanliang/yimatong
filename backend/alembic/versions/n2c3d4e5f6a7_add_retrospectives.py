"""add tenant-scoped pilot retrospectives

Revision ID: n2c3d4e5f6a7
Revises: m1b2c3d4e5f6
Create Date: 2026-08-06

试点复盘实体表（beads: yimatong-bgag.2，PRD pilot-learning-retrospective §4.3/§6.1/§7）。
带 tenant_id 并启用 RLS，(tenant_id, period_day) 唯一保证 poller 幂等生成。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n2c3d4e5f6a7"
down_revision: str | None = "m1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrospectives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("period_day", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_review_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("scorecard_snapshot", sa.JSON(), nullable=False),
        sa.Column("issues", sa.Text(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Uuid(), nullable=True),
        sa.Column("supplementary_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["completed_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "period_day", name="uq_retrospectives_tenant_period"),
    )
    op.create_index("ix_retrospectives_tenant_id", "retrospectives", ["tenant_id"], unique=False)
    op.create_index(
        "ix_retrospectives_tenant_period",
        "retrospectives",
        ["tenant_id", "period_day"],
        unique=False,
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE retrospectives ENABLE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON retrospectives")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON retrospectives
            USING (
                tenant_id = current_tenant_id()
                OR (
                    current_tenant_id() IS NULL
                    AND current_setting('app.bypass_rls', true) = 'true'
                )
            )
            WITH CHECK (
                tenant_id = current_tenant_id()
                OR (
                    current_tenant_id() IS NULL
                    AND current_setting('app.bypass_rls', true) = 'true'
                )
            )
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON retrospectives")
        op.execute("ALTER TABLE retrospectives DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_retrospectives_tenant_period", table_name="retrospectives")
    op.drop_index("ix_retrospectives_tenant_id", table_name="retrospectives")
    op.drop_table("retrospectives")
