"""expand confirmed GMV attribution authority

Revision ID: u8b0c1d2e3f4
Revises: u8a3f4a5b6c7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u8a3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "gmv_attributions",
        sa.Column(
            "authority_status",
            sa.String(30),
            nullable=False,
            server_default=sa.text("'legacy_quarantined'"),
        ),
    )
    op.execute(
        "ALTER TABLE public.gmv_attributions ADD CONSTRAINT ck_gmv_attributions_authority_status_u8b "
        "CHECK (authority_status IN ('legacy_quarantined','confirmed')) NOT VALID"
    )
    op.execute("ALTER TABLE public.gmv_attributions VALIDATE CONSTRAINT ck_gmv_attributions_authority_status_u8b")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_gmv_attributions_authority_status_u8b", "gmv_attributions", type_="check")
    op.drop_column("gmv_attributions", "authority_status")
