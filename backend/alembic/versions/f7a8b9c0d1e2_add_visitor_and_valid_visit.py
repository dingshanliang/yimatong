"""add anonymous visitors and scan_events.is_valid_visit

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-27 16:00:00.000000

yimatong-zgb1.10：增长转化线主干。
- 新建 anonymous_visitors 表（first-party 稳定访客标识，Decision 22）
- scan_events 加 is_valid_visit 列（Decision 20 有效访问过滤）
- scan_events 加 visitor_id 列（关联访客，跨会话稳定）
兼容期：旧行 is_valid_visit 默认 false，visitor_id 允许 NULL。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. anonymous_visitors 表
    op.create_table(
        "anonymous_visitors",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_id", sa.String(length=64), nullable=False),
        sa.Column(
            "consumer_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("first_environment", sa.String(length=20), nullable=True),
        sa.Column("first_ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "last_seen_at",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("visitor_id", name="uq_anonymous_visitors_visitor_id"),
        sa.ForeignKeyConstraint(["consumer_id"], ["consumer_profiles.id"]),
    )
    op.create_index("ix_anonymous_visitors_tenant_id", "anonymous_visitors", ["tenant_id"])
    op.create_index("ix_anonymous_visitors_visitor_id", "anonymous_visitors", ["visitor_id"])
    op.create_index(
        "ix_visitors_tenant_consumer", "anonymous_visitors", ["tenant_id", "consumer_id"]
    )

    # 2. scan_events 加 is_valid_visit + visitor_id 列
    op.add_column(
        "scan_events",
        sa.Column("is_valid_visit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "scan_events",
        sa.Column("visitor_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_scan_events_valid_visit",
        "scan_events",
        ["tenant_id", "is_valid_visit"],
    )


def downgrade() -> None:
    op.drop_index("ix_scan_events_valid_visit", table_name="scan_events")
    op.drop_column("scan_events", "visitor_id")
    op.drop_column("scan_events", "is_valid_visit")
    op.drop_index("ix_visitors_tenant_consumer", table_name="anonymous_visitors")
    op.drop_index("ix_anonymous_visitors_visitor_id", table_name="anonymous_visitors")
    op.drop_index("ix_anonymous_visitors_tenant_id", table_name="anonymous_visitors")
    op.drop_table("anonymous_visitors")
