"""link benefit deliveries to claims

Revision ID: 0a1b2c3d4e5f
Revises: f4a5b6c7d8e9
Create Date: 2026-06-01 18:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0a1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("benefit_claims", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.add_column("benefit_deliveries", sa.Column("benefit_id", sa.Uuid(), nullable=True))
    op.add_column("benefit_deliveries", sa.Column("claim_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_benefit_deliveries_benefit_id"), "benefit_deliveries", ["benefit_id"], unique=False)
    op.create_index(op.f("ix_benefit_deliveries_claim_id"), "benefit_deliveries", ["claim_id"], unique=False)
    op.execute(
        """
        UPDATE benefit_deliveries AS d
        SET
            benefit_id = c.benefit_id,
            claim_id = c.id
        FROM benefit_claims AS c
        JOIN benefits AS b ON b.id = c.benefit_id
        WHERE d.tenant_id = c.tenant_id
          AND d.consumer_id = c.consumer_id
          AND d.connector_id = b.connector_id
          AND d.claim_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_benefit_deliveries_claim_id"), table_name="benefit_deliveries")
    op.drop_index(op.f("ix_benefit_deliveries_benefit_id"), table_name="benefit_deliveries")
    op.drop_column("benefit_deliveries", "claim_id")
    op.drop_column("benefit_deliveries", "benefit_id")
    op.execute("UPDATE benefit_claims SET created_at = now() WHERE created_at IS NULL")
    op.alter_column("benefit_claims", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
