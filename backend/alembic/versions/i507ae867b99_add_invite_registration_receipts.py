"""add invite registration idempotency receipts

Revision ID: i507ae867b99
Revises: h496fd756a88
Create Date: 2026-08-03 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "i507ae867b99"
down_revision: str | None = "h496fd756a88"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    # Public/platform control table: registration starts before a tenant exists,
    # so it deliberately has no tenant ownership column and no tenant RLS policy.
    op.create_table(
        "invite_registration_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_slug", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(tenant_id IS NULL AND tenant_slug IS NULL) OR "
            "(tenant_id IS NOT NULL AND tenant_slug IS NOT NULL)",
            name="ck_invite_registration_receipt_completion_pair",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key_hash"),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.drop_table("invite_registration_receipts")
