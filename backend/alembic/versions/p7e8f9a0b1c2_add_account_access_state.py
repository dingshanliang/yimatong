"""add account access state

Revision ID: p7e8f9a0b1c2
Revises: o6d7e8f9a0b1
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "p7e8f9a0b1c2"
down_revision: str | None = "o6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "accounts",
        sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "platform_audit_log",
        sa.Column("details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_audit_log", "details")
    op.drop_column("accounts", "auth_version")
    op.drop_column("accounts", "is_active")
