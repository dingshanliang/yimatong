"""add batch delivery states (printing/delivered/exported) to PG enum

Revision ID: c7d8e9f0a1b2
Revises: f8b1b3069972
Create Date: 2026-07-27 12:00:00.000000

背景（yimatong-zgb1.3）：
CodeBatchStatus 在 Python enum 里有 printing/delivered/exported，但 PG enum 类型
codebatchstatus 从未 ALTER TYPE 加入这些值（723345d2b4c1 是空 no-op 迁移）。
导致 mark_printing/mark_delivered 在生产 PG 上 commit 会失败。

本迁移补齐 PG enum 值，使批次交付进度状态（与单码消费生命周期分离，Decision 10）
可持久化。SQLite/其他方言不强制 enum 类型，跳过。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "f8b1b3069972"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE 不能在事务里执行，用 autocommit_block
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE codebatchstatus ADD VALUE IF NOT EXISTS 'printing'")
            op.execute("ALTER TYPE codebatchstatus ADD VALUE IF NOT EXISTS 'delivered'")
            op.execute("ALTER TYPE codebatchstatus ADD VALUE IF NOT EXISTS 'exported'")
        return

    # SQLite/其他方言不强制 PG enum 类型，无需操作


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(
        """
        DO $block$
        DECLARE protected_batches text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO protected_batches
            FROM public.code_batches
            WHERE status::text IN ('printing', 'delivered', 'exported');
            IF protected_batches IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'Cannot discard code batch delivery state; batch ids: ' || protected_batches;
            END IF;
        END
        $block$
        """
    )

    # PG 不支持直接删除 enum 值；仅在没有交付态数据时重建旧 enum。
    op.execute("ALTER TYPE codebatchstatus RENAME TO codebatchstatus_old")
    new_enum = sa.Enum(
        "pending",
        "generating",
        "completed",
        "activated",
        "failed",
        name="codebatchstatus",
    )
    new_enum.create(bind, checkfirst=False)
    op.execute("ALTER TABLE code_batches ALTER COLUMN status TYPE codebatchstatus USING status::text::codebatchstatus")
    op.execute("DROP TYPE codebatchstatus_old")
