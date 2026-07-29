"""add tenant brand_profile

租户级品牌定制槽位（H5 受控定制，beads: yimatong-z6i0.10）。
JSONB 结构：{primary_color, radius_preset, background_preset, hide_yimatong_brand}

Revision ID: q8f9a0b1c2d3
Revises: p7e8f9a0b1c2
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "q8f9a0b1c2d3"
down_revision: str | None = "p7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("brand_profile", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "brand_profile")
