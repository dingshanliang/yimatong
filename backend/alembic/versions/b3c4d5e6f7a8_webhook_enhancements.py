"""webhook models: add fields for EPIC-19 integration bus

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-05-30

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- webhook_endpoints ---
    op.add_column("webhook_endpoints", sa.Column("description", sa.String(500), nullable=True))
    op.add_column("webhook_endpoints", sa.Column("batch_mode", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column(
        "webhook_endpoints",
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- api_keys ---
    op.add_column(
        "api_keys",
        sa.Column("role", sa.String(50), nullable=False, server_default="data_reader"),
    )
    op.add_column("api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "api_keys",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- webhook_deliveries ---
    op.add_column("webhook_deliveries", sa.Column("event_id", sa.String(36), nullable=False, server_default=""))
    op.add_column(
        "webhook_deliveries",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("webhook_deliveries", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_deliveries", sa.Column("last_response_code", sa.Integer(), nullable=True))
    op.add_column("webhook_deliveries", sa.Column("last_response_body", sa.Text(), nullable=True))
    op.add_column(
        "webhook_deliveries",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])


def downgrade() -> None:
    # --- webhook_deliveries ---
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.drop_column("webhook_deliveries", "updated_at")
    op.drop_column("webhook_deliveries", "created_at")
    op.drop_column("webhook_deliveries", "last_response_body")
    op.drop_column("webhook_deliveries", "last_response_code")
    op.drop_column("webhook_deliveries", "next_retry_at")
    op.drop_column("webhook_deliveries", "retry_count")
    op.drop_column("webhook_deliveries", "event_id")

    # --- api_keys ---
    op.drop_column("api_keys", "updated_at")
    op.drop_column("api_keys", "created_at")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "role")

    # --- webhook_endpoints ---
    op.drop_column("webhook_endpoints", "updated_at")
    op.drop_column("webhook_endpoints", "created_at")
    op.drop_column("webhook_endpoints", "batch_size")
    op.drop_column("webhook_endpoints", "batch_mode")
    op.drop_column("webhook_endpoints", "description")
