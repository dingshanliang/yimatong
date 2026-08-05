"""require initial password change

Revision ID: g385ec645f77
Revises: f274db534e66
Create Date: 2026-08-03 17:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g385ec645f77"
down_revision: str | None = "f274db534e66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("accounts", "must_change_password")
