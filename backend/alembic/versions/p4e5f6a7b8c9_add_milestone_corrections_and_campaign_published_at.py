"""add milestone corrections + campaign published_at

Revision ID: p4e5f6a7b8c9
Revises: o3d4e5f6a7b8
Create Date: 2026-08-07

两项 spec 闭合（beads: yimatong-bgag.7，PRD pilot-learning-retrospective §6.2/§4.1）：

1. §6.2 更正记录机制：新增 pilot_milestone_corrections 追加式更正表（带 RLS），
   不改写 pilot_milestones.achieved_at 原始事实。
2. §4.1 里程碑 5 事实源：campaigns.published_at 首次发布时间戳列（nullable），
   change_campaign_status 首次切到 ACTIVE 时写入；里程碑派生据此填充
   first_campaign_published。

回滚路径：删表/删列均无数据回填需求（纯结构变更），逆序安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "p4e5f6a7b8c9"
down_revision: str | None = "o3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 更正记录表（PRD §6.2）
    op.create_table(
        "pilot_milestone_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("milestone_id", sa.Uuid(), nullable=False),
        sa.Column("milestone_type", sa.String(length=40), nullable=False),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["milestone_id"], ["pilot_milestones.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corrected_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_milestone_corrections_tenant_id",
        "pilot_milestone_corrections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_milestone_corrections_tenant_milestone",
        "pilot_milestone_corrections",
        ["tenant_id", "milestone_id", "created_at"],
        unique=False,
    )

    # 2. 活动发布时间戳列（里程碑 5 事实源）
    op.add_column("campaigns", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_campaigns_published_at", "campaigns", ["tenant_id", "published_at"], unique=False)

    # RLS（仅 PostgreSQL；SQLite 测试中 RLS 为 no-op）
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE pilot_milestone_corrections ENABLE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON pilot_milestone_corrections")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON pilot_milestone_corrections
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON pilot_milestone_corrections")
        op.execute("ALTER TABLE pilot_milestone_corrections DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_campaigns_published_at", table_name="campaigns")
    op.drop_column("campaigns", "published_at")
    op.drop_index(
        "ix_pilot_milestone_corrections_tenant_milestone",
        table_name="pilot_milestone_corrections",
    )
    op.drop_index(
        "ix_pilot_milestone_corrections_tenant_id",
        table_name="pilot_milestone_corrections",
    )
    op.drop_table("pilot_milestone_corrections")
