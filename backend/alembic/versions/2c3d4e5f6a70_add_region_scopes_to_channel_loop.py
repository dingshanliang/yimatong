"""add region scopes to channel loop

Revision ID: 2c3d4e5f6a70
Revises: 1b2c3d4e5f60
Create Date: 2026-06-01 21:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2c3d4e5f6a70"
down_revision: str | Sequence[str] | None = "1b2c3d4e5f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("code_allocations", sa.Column("region_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_code_allocations_region_id_regions",
        "code_allocations",
        "regions",
        ["region_id"],
        ["id"],
    )
    op.create_index(op.f("ix_code_allocations_region_id"), "code_allocations", ["region_id"], unique=False)

    op.add_column("diversion_clues", sa.Column("region_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_diversion_clues_region_id"), "diversion_clues", ["region_id"], unique=False)

    op.add_column("account_channel_scopes", sa.Column("region_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_account_channel_scopes_region_id_regions",
        "account_channel_scopes",
        "regions",
        ["region_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_account_channel_scopes_region_id"),
        "account_channel_scopes",
        ["region_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_channel_scopes_region_id"), table_name="account_channel_scopes")
    op.drop_constraint("fk_account_channel_scopes_region_id_regions", "account_channel_scopes", type_="foreignkey")
    op.drop_column("account_channel_scopes", "region_id")

    op.drop_index(op.f("ix_diversion_clues_region_id"), table_name="diversion_clues")
    op.drop_column("diversion_clues", "region_id")

    op.drop_index(op.f("ix_code_allocations_region_id"), table_name="code_allocations")
    op.drop_constraint("fk_code_allocations_region_id_regions", "code_allocations", type_="foreignkey")
    op.drop_column("code_allocations", "region_id")
