"""add_product_profile_assets

Revision ID: c4e5f6a7b8c9
Revises: 9e93374ca54c
Create Date: 2026-05-31 14:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e5f6a7b8c9"
down_revision: str | None = "9e93374ca54c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


asset_type_enum = sa.Enum(
    "image",
    "video",
    "test_report",
    "certificate",
    "story",
    "other",
    name="productassettype",
)
asset_status_enum = sa.Enum("active", "inactive", name="productassetstatus")


def upgrade() -> None:
    op.add_column("products", sa.Column("origin", sa.String(length=200), nullable=True))
    op.add_column("products", sa.Column("image_url", sa.String(length=500), nullable=True))
    op.add_column("products", sa.Column("story_title", sa.String(length=200), nullable=True))
    op.add_column("products", sa.Column("story_content", sa.Text(), nullable=True))

    op.add_column("skus", sa.Column("package_type", sa.String(length=100), nullable=True))
    op.add_column("skus", sa.Column("barcode", sa.String(length=100), nullable=True))
    op.add_column("skus", sa.Column("image_url", sa.String(length=500), nullable=True))

    op.add_column("production_batches", sa.Column("origin", sa.String(length=200), nullable=True))

    op.create_table(
        "product_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("asset_type", asset_type_enum, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("issuer", sa.String(length=200), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("file_url", sa.String(length=500), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("status", asset_status_enum, nullable=False, server_default="active"),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_product_assets_tenant_id"), "product_assets", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_product_assets_product_id"), "product_assets", ["product_id"], unique=False)
    op.create_index(op.f("ix_product_assets_asset_type"), "product_assets", ["asset_type"], unique=False)

    op.execute("ALTER TABLE product_assets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_assets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_product_assets
        ON product_assets
        USING (tenant_id = current_tenant_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_product_assets ON product_assets")
    op.execute("ALTER TABLE product_assets DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_product_assets_asset_type"), table_name="product_assets")
    op.drop_index(op.f("ix_product_assets_product_id"), table_name="product_assets")
    op.drop_index(op.f("ix_product_assets_tenant_id"), table_name="product_assets")
    op.drop_table("product_assets")
    asset_status_enum.drop(op.get_bind(), checkfirst=True)
    asset_type_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_column("production_batches", "origin")

    op.drop_column("skus", "image_url")
    op.drop_column("skus", "barcode")
    op.drop_column("skus", "package_type")

    op.drop_column("products", "story_content")
    op.drop_column("products", "story_title")
    op.drop_column("products", "image_url")
    op.drop_column("products", "origin")
