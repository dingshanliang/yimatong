"""add ops_task_id link on retrospectives

Revision ID: o3d4e5f6a7b8
Revises: n2c3d4e5f6a7
Create Date: 2026-08-06

试点复盘关联 OpsTask 提醒入口（beads: yimatong-bgag.4，PRD §4.2/§6.3）。
retrospectives.ops_task_id nullable FK → ops_tasks.id，nullable 保证旧数据兼容。
注：此迁移前已存在的复盘行 ops_task_id 为 NULL，不回填 OpsTask（增量特性）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o3d4e5f6a7b8"
down_revision: str | None = "n2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrospectives",
        sa.Column("ops_task_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_retrospectives_ops_task_id",
        "retrospectives",
        "ops_tasks",
        ["ops_task_id"],
        ["id"],
    )
    op.create_index("ix_retrospectives_ops_task_id", "retrospectives", ["ops_task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_retrospectives_ops_task_id", table_name="retrospectives")
    op.drop_constraint("fk_retrospectives_ops_task_id", "retrospectives", type_="foreignkey")
    op.drop_column("retrospectives", "ops_task_id")
