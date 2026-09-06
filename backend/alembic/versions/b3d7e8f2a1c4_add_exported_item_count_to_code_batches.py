"""Add exported_item_count to code_batches for exclusion-aware exports.

Revision ID: b3d7e8f2a1c4
Revises: a7f2c9d41e58
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3d7e8f2a1c4"
down_revision: str | Sequence[str] | None = "a7f2c9d41e58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "exported_item_count IS NULL OR (exported_item_count BETWEEN 1 AND 10000)"
_CONSTRAINT = "ck_code_batches_exported_item_count_cap"
_COLUMN = "exported_item_count"


def upgrade() -> None:
    op.add_column(
        "code_batches",
        sa.Column(_COLUMN, sa.Integer(), nullable=True),
    )
    op.create_check_constraint(_CONSTRAINT, "code_batches", _CHECK)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "code_batches", type_="check")
    op.drop_column("code_batches", _COLUMN)
