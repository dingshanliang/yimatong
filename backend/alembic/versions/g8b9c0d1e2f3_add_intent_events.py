"""add intent events table

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-27 17:00:00.000000

yimatong-zgb1.10：意图事件持久化（Decision 19）。
page_view / benefit_click / lead_click / wecom_click / mall_redirect。
幂等：(tenant_id, visitor_id, event_type, client_event_id)。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intent_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("public_id", sa.String(length=20), nullable=True),
        sa.Column("visitor_id", sa.String(length=64), nullable=True),
        sa.Column("client_event_id", sa.String(length=100), nullable=True),
        sa.Column("page_version_id", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_intent_events_tenant_id", "intent_events", ["tenant_id"])
    op.create_index("ix_intent_events_public_id", "intent_events", ["public_id"])
    op.create_index("ix_intent_events_visitor_id", "intent_events", ["visitor_id"])
    op.create_index(
        "ix_intent_events_tenant_type", "intent_events", ["tenant_id", "event_type"]
    )
    op.create_index(
        "ix_intent_events_idempotent",
        "intent_events",
        ["tenant_id", "visitor_id", "event_type", "client_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_intent_events_idempotent", table_name="intent_events")
    op.drop_index("ix_intent_events_tenant_type", table_name="intent_events")
    op.drop_index("ix_intent_events_visitor_id", table_name="intent_events")
    op.drop_index("ix_intent_events_public_id", table_name="intent_events")
    op.drop_index("ix_intent_events_tenant_id", table_name="intent_events")
    op.drop_table("intent_events")
