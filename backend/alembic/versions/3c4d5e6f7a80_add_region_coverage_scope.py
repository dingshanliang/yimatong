"""add region coverage scope

Revision ID: 3c4d5e6f7a80
Revises: 2c3d4e5f6a70
Create Date: 2026-06-01 22:40:00.000000

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3c4d5e6f7a80"
down_revision: str | Sequence[str] | None = "2c3d4e5f6a70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "regions",
        sa.Column("coverage_type", sa.String(length=30), server_default="city", nullable=False),
    )
    op.add_column("regions", sa.Column("coverage_areas", sa.JSON(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("select id, province, city from regions")).mappings().all()
    for row in rows:
        province = row["province"]
        city = row["city"]
        if province or city:
            coverage_areas = [{"province": province, "city": city}]
            coverage_type = "city" if city else "province"
        else:
            coverage_areas = []
            coverage_type = "city"
        bind.execute(
            sa.text(
                "update regions set coverage_type = :coverage_type, "
                "coverage_areas = cast(:coverage_areas as json) where id = :id"
            ),
            {"coverage_type": coverage_type, "coverage_areas": json.dumps(coverage_areas), "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_column("regions", "coverage_areas")
    op.drop_column("regions", "coverage_type")
