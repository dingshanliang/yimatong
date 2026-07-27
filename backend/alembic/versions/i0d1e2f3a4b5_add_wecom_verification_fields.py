"""add wecom verification fields

Revision ID: i0d1e2f3a4b5
Revises: h9c0d1e2f3a4
Create Date: 2026-07-27 19:00:00.000000

yimatong-zgb1.12 Decision 26：企微确认转化需区分验签来源、事件类型、指纹幂等、待验证。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "i0d1e2f3a4b5"
down_revision: str | None = "h9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wecom_external_contacts",
        sa.Column("verification_source", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "wecom_external_contacts",
        sa.Column("change_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "wecom_external_contacts",
        sa.Column("event_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "wecom_external_contacts",
        sa.Column("welcome_code_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_wecom_external_contacts_fingerprint",
        "wecom_external_contacts",
        ["tenant_id", "event_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wecom_external_contacts_fingerprint", table_name="wecom_external_contacts"
    )
    op.drop_column("wecom_external_contacts", "welcome_code_pending")
    op.drop_column("wecom_external_contacts", "event_fingerprint")
    op.drop_column("wecom_external_contacts", "change_type")
    op.drop_column("wecom_external_contacts", "verification_source")
