"""add campaign product link

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f2a3
Create Date: 2026-06-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_campaigns_product_id"), "campaigns", ["product_id"], unique=False)
    op.create_foreign_key(
        "fk_campaigns_product_id_products",
        "campaigns",
        "products",
        ["product_id"],
        ["id"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE campaigns
            SET product_id = (rules_json ->> 'product_id')::uuid
            WHERE product_id IS NULL
              AND rules_json::jsonb ? 'product_id'
              AND rules_json ->> 'product_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            """
        )


def downgrade() -> None:
    op.drop_constraint("fk_campaigns_product_id_products", "campaigns", type_="foreignkey")
    op.drop_index(op.f("ix_campaigns_product_id"), table_name="campaigns")
    op.drop_column("campaigns", "product_id")
