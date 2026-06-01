"""add point redemptions and product controls

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-01 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "point_transactions",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_point_transactions_tenant_created",
        "point_transactions",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.add_column("point_products", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("point_products", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "point_products",
        sa.Column("per_consumer_limit", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "point_products",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_point_products_tenant_enabled_sort",
        "point_products",
        ["tenant_id", "enabled", "sort_order"],
        unique=False,
    )

    op.create_table(
        "point_redemptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=False),
        sa.Column("point_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("benefit_id", sa.Uuid(), nullable=True),
        sa.Column("benefit_claim_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["point_transaction_id"], ["point_transactions.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["point_products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_point_redemptions_tenant_id"), "point_redemptions", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_point_redemptions_consumer_id"), "point_redemptions", ["consumer_id"], unique=False)
    op.create_index(op.f("ix_point_redemptions_product_id"), "point_redemptions", ["product_id"], unique=False)
    op.create_index(
        op.f("ix_point_redemptions_point_transaction_id"),
        "point_redemptions",
        ["point_transaction_id"],
        unique=False,
    )
    op.create_index(op.f("ix_point_redemptions_benefit_id"), "point_redemptions", ["benefit_id"], unique=False)
    op.create_index(
        op.f("ix_point_redemptions_benefit_claim_id"),
        "point_redemptions",
        ["benefit_claim_id"],
        unique=False,
    )
    op.create_index(
        "ix_point_redemptions_tenant_created",
        "point_redemptions",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_point_redemptions_tenant_product",
        "point_redemptions",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "ix_point_redemptions_tenant_consumer",
        "point_redemptions",
        ["tenant_id", "consumer_id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE point_redemptions ENABLE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON point_redemptions")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON point_redemptions
            USING (
                tenant_id = current_tenant_id()
                OR (
                    current_tenant_id() IS NULL
                    AND current_setting('app.bypass_rls', true) = 'true'
                )
            )
            WITH CHECK (
                tenant_id = current_tenant_id()
                OR (
                    current_tenant_id() IS NULL
                    AND current_setting('app.bypass_rls', true) = 'true'
                )
            )
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON point_redemptions")
        op.execute("ALTER TABLE point_redemptions DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_point_redemptions_tenant_consumer", table_name="point_redemptions")
    op.drop_index("ix_point_redemptions_tenant_product", table_name="point_redemptions")
    op.drop_index("ix_point_redemptions_tenant_created", table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_benefit_claim_id"), table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_benefit_id"), table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_point_transaction_id"), table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_product_id"), table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_consumer_id"), table_name="point_redemptions")
    op.drop_index(op.f("ix_point_redemptions_tenant_id"), table_name="point_redemptions")
    op.drop_table("point_redemptions")

    op.drop_index("ix_point_products_tenant_enabled_sort", table_name="point_products")
    op.drop_column("point_products", "sort_order")
    op.drop_column("point_products", "per_consumer_limit")
    op.drop_column("point_products", "ends_at")
    op.drop_column("point_products", "starts_at")

    op.drop_index("ix_point_transactions_tenant_created", table_name="point_transactions")
    op.drop_column("point_transactions", "created_at")
