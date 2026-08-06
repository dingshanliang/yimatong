"""add tenant-scoped pilot milestones

Revision ID: m1b2c3d4e5f6
Revises: k629cf089d21
Create Date: 2026-08-06

试点里程碑记录表（beads: yimatong-bgag.1，PRD pilot-learning-retrospective §4.1/§6.2）。
带 tenant_id 并启用 RLS，(tenant_id, milestone_type) 唯一保证幂等派生。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m1b2c3d4e5f6"
down_revision: str | None = "k629cf089d21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pilot_milestones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("milestone_type", sa.String(length=40), nullable=False),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "milestone_type", name="uq_pilot_milestones_tenant_type"),
    )
    op.create_index("ix_pilot_milestones_tenant_id", "pilot_milestones", ["tenant_id"], unique=False)
    op.create_index(
        "ix_pilot_milestones_tenant_type",
        "pilot_milestones",
        ["tenant_id", "milestone_type"],
        unique=False,
    )
    # RLS（仅 PostgreSQL；SQLite 测试中 RLS 为 no-op，查询靠 tenant_id 过滤手工隔离）
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE pilot_milestones ENABLE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON pilot_milestones")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON pilot_milestones
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON pilot_milestones")
        op.execute("ALTER TABLE pilot_milestones DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_pilot_milestones_tenant_type", table_name="pilot_milestones")
    op.drop_index("ix_pilot_milestones_tenant_id", table_name="pilot_milestones")
    op.drop_table("pilot_milestones")
