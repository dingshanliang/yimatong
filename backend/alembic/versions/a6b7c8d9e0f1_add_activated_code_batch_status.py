"""add_activated_code_batch_status

Revision ID: a6b7c8d9e0f1
Revises: f5a1b2c3d4e5
Create Date: 2026-05-31 22:38:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f5a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE codebatchstatus ADD VALUE IF NOT EXISTS 'activated'")
        return

    # SQLite and other test dialects do not enforce the PostgreSQL enum type.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("UPDATE code_batches SET status = 'completed' WHERE status = 'activated'")
    op.execute("ALTER TYPE codebatchstatus RENAME TO codebatchstatus_old")
    new_enum = sa.Enum("pending", "generating", "completed", "failed", name="codebatchstatus")
    new_enum.create(bind, checkfirst=False)
    op.execute(
        """
        ALTER TABLE code_batches
        ALTER COLUMN status TYPE codebatchstatus
        USING status::text::codebatchstatus
        """
    )
    op.execute("DROP TYPE codebatchstatus_old")
