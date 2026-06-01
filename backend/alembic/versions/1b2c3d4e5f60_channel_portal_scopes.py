"""channel portal scopes and diversion resolution fields

Revision ID: 1b2c3d4e5f60
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-01 18:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "1b2c3d4e5f60"
down_revision: str | Sequence[str] | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("distributors", "regions", "stores"):
        op.add_column(
            table,
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.add_column(
            table,
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    op.add_column("regions", sa.Column("status", sa.String(length=20), server_default="active", nullable=False))

    op.add_column("diversion_clues", sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column("diversion_clues", sa.Column("resolved_by_account_id", sa.Uuid(), nullable=True))
    op.add_column("diversion_clues", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f("ix_diversion_clues_resolved_by_account_id"),
        "diversion_clues",
        ["resolved_by_account_id"],
        unique=False,
    )

    op.create_table(
        "account_channel_scopes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("distributor_id", sa.Uuid(), nullable=True),
        sa.Column("store_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["distributor_id"], ["distributors.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_channel_scopes_account_id"), "account_channel_scopes", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_account_channel_scopes_distributor_id"),
        "account_channel_scopes",
        ["distributor_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_channel_scope_unique",
        "account_channel_scopes",
        ["tenant_id", "account_id", "scope_type"],
        unique=True,
    )
    op.create_index(op.f("ix_account_channel_scopes_store_id"), "account_channel_scopes", ["store_id"], unique=False)
    op.create_index(op.f("ix_account_channel_scopes_tenant_id"), "account_channel_scopes", ["tenant_id"], unique=False)
    op.execute("ALTER TABLE account_channel_scopes ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON account_channel_scopes
        USING (tenant_id = current_tenant_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON account_channel_scopes")
    op.execute("ALTER TABLE account_channel_scopes DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_account_channel_scopes_tenant_id"), table_name="account_channel_scopes")
    op.drop_index(op.f("ix_account_channel_scopes_store_id"), table_name="account_channel_scopes")
    op.drop_index("ix_account_channel_scope_unique", table_name="account_channel_scopes")
    op.drop_index(op.f("ix_account_channel_scopes_distributor_id"), table_name="account_channel_scopes")
    op.drop_index(op.f("ix_account_channel_scopes_account_id"), table_name="account_channel_scopes")
    op.drop_table("account_channel_scopes")

    op.drop_index(op.f("ix_diversion_clues_resolved_by_account_id"), table_name="diversion_clues")
    op.drop_column("diversion_clues", "resolved_at")
    op.drop_column("diversion_clues", "resolved_by_account_id")
    op.drop_column("diversion_clues", "resolution_note")

    op.drop_column("regions", "status")
    for table in ("stores", "regions", "distributors"):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
