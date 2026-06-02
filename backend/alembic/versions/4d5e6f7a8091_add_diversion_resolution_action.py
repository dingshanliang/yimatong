"""add diversion resolution action

Revision ID: 4d5e6f7a8091
Revises: 3c4d5e6f7a80
Create Date: 2026-06-01 23:52:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d5e6f7a8091"
down_revision: str | Sequence[str] | None = "3c4d5e6f7a80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("diversion_clues", sa.Column("resolution_action", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("diversion_clues", "resolution_action")
