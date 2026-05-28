"""convert business datetime columns to TIMESTAMPTZ

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-28

All business datetime columns were bare TIMESTAMP, causing type mismatches
with the base model's TIMESTAMPTZ created_at/updated_at and asyncpg's
strict timezone handling. Convert them all to TIMESTAMPTZ for consistency.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# (table, column) pairs to convert
_COLUMNS = [
    ("code_items", "activated_at"),
    ("code_items", "bound_at"),
    ("code_items", "revoked_at"),
    ("accounts", "locked_until"),
    ("accounts", "last_login_at"),
    ("scan_events", "scan_time"),
    ("platform_audit_log", "timestamp"),
    ("external_orders", "order_time"),
    ("redpacket_rules", "start_time"),
    ("redpacket_rules", "end_time"),
]


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            postgresql_using=f"{column}::timestamptz",
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            postgresql_using=f"{column}::timestamp",
        )
