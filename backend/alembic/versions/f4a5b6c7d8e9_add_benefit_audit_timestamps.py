"""add benefit audit timestamps

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-01 17:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benefits",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "benefits",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "benefit_claims",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "benefit_claims",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.alter_column("benefit_claims", "campaign_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS(SELECT 1 FROM public.benefit_claims WHERE campaign_id IS NULL) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='standalone benefit claim facts block downgrade',
                    HINT='Retain standalone claim history or abort downgrade; downgrade never deletes claim facts.';
            END IF;
        END
        $block$
        """
    )
    op.alter_column("benefit_claims", "campaign_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("benefit_claims", "updated_at")
    op.drop_column("benefit_claims", "created_at")
    op.drop_column("benefits", "updated_at")
    op.drop_column("benefits", "created_at")
