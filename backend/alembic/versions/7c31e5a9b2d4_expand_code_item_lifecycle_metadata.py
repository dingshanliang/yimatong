"""Expand nullable code-item lifecycle metadata without changing runtime authority.

Revision ID: 7c31e5a9b2d4
Revises: 4a92d1e3f5b7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c31e5a9b2d4"
down_revision: str | None = "4a92d1e3f5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight() -> None:
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.code_items
                WHERE freeze_provenance_version = 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard version-1 frozen provenance';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.interception_records
                WHERE code_item_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard risk interception code-item evidence';
            END IF;
        END
        $block$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column("code_items", sa.Column("frozen_from_status", sa.String(16), nullable=True))
    op.add_column("code_items", sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("code_items", sa.Column("frozen_by", sa.String(64), nullable=True))
    op.add_column("code_items", sa.Column("freeze_reason", sa.String(200), nullable=True))
    op.add_column("code_items", sa.Column("freeze_provenance_version", sa.SmallInteger(), nullable=True))
    op.add_column("interception_records", sa.Column("code_item_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    _preflight()
    op.drop_column("interception_records", "code_item_id")
    op.drop_column("code_items", "freeze_provenance_version")
    op.drop_column("code_items", "freeze_reason")
    op.drop_column("code_items", "frozen_by")
    op.drop_column("code_items", "frozen_at")
    op.drop_column("code_items", "frozen_from_status")
