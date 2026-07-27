"""add attribution snapshot fields

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-07-27 21:00:00.000000

yimatong-zgb1.14 Decision 33：GmvAttribution 加快照字段（product_id/code_batch_id/channel/page_version_id/original_amount）。
归因写入时快照，后续配置变更不改写历史。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k2f3a4b5c6d7"
down_revision: str | None = "j1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gmv_attributions",
        sa.Column("product_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "gmv_attributions",
        sa.Column("code_batch_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "gmv_attributions",
        sa.Column("channel_snapshot", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "gmv_attributions",
        sa.Column("page_version_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "gmv_attributions",
        sa.Column("original_amount", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_gmv_attributions_product_id", "gmv_attributions", ["product_id"])
    op.create_index("ix_gmv_attributions_code_batch_id", "gmv_attributions", ["code_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_gmv_attributions_code_batch_id", table_name="gmv_attributions")
    op.drop_index("ix_gmv_attributions_product_id", table_name="gmv_attributions")
    op.drop_column("gmv_attributions", "original_amount")
    op.drop_column("gmv_attributions", "page_version_id")
    op.drop_column("gmv_attributions", "channel_snapshot")
    op.drop_column("gmv_attributions", "code_batch_id")
    op.drop_column("gmv_attributions", "product_id")
