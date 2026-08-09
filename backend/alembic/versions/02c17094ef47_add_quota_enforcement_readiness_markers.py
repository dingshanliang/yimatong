"""Add quota enforcement readiness markers.

Revision ID: 02c17094ef47
Revises: 7d4e91a6c2bf
Create Date: 2026-08-09 16:42:22.882842

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "02c17094ef47"
down_revision: str | None = "7d4e91a6c2bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_quota_usage"
_READY_CONSTRAINT = "ck_tenant_quota_usage_ready_provenance"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    op.add_column(_TABLE, sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("source_revision", sa.String(length=64), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("enforcement_ready", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        _READY_CONSTRAINT,
        _TABLE,
        "NOT enforcement_ready OR "
        "(reconciled_at IS NOT NULL AND source_revision IS NOT NULL AND length(trim(source_revision)) > 0)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")
    op.drop_constraint(_READY_CONSTRAINT, _TABLE, type_="check")
    op.drop_column(_TABLE, "enforcement_ready")
    op.drop_column(_TABLE, "source_revision")
    op.drop_column(_TABLE, "reconciled_at")
