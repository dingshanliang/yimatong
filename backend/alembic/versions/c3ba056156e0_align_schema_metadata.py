"""Align schema metadata and restore missing tenant isolation.

Revision ID: c3ba056156e0
Revises: q8f9a0b1c2d3
Create Date: 2026-07-30 17:43:44.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3ba056156e0"
down_revision: str | None = "q8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Platform-global invite codes are created by platform administrators and
    # consumed before a tenant context exists, so the table intentionally has
    # no tenant_id and no RLS policy.
    invite_code_status = sa.Enum(
        "active",
        "inactive",
        "expired",
        "depleted",
        name="invitecodestatus",
    )
    op.create_table(
        "tenant_invite_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("tenant_type", sa.String(length=20), server_default="brand", nullable=False),
        sa.Column("max_uses", sa.Integer(), server_default="1", nullable=False),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", invite_code_status, server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tenant_invite_codes_code",
        "tenant_invite_codes",
        ["code"],
        unique=True,
    )

    # These existing tables are small. Server defaults safely populate rows.
    op.add_column(
        "plan_definitions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "tenant_health_metrics",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.alter_column(
        "tenants",
        "brand_profile",
        existing_type=sa.JSON(),
        existing_nullable=True,
        comment="租户品牌定制槽位：primary_color/radius_preset/background_preset/hide_yimatong_brand",
    )

    for table_name in ("anonymous_visitors", "tenant_health_metrics"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table_name}
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
        """)


def downgrade() -> None:
    for table_name in ("anonymous_visitors", "tenant_health_metrics"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    op.alter_column(
        "tenants",
        "brand_profile",
        existing_type=sa.JSON(),
        existing_nullable=True,
        existing_comment="租户品牌定制槽位：primary_color/radius_preset/background_preset/hide_yimatong_brand",
        comment=None,
    )
    op.drop_column("tenant_health_metrics", "created_at")
    op.drop_column("plan_definitions", "updated_at")
    op.drop_index("ix_tenant_invite_codes_code", table_name="tenant_invite_codes")
    op.drop_table("tenant_invite_codes")
    sa.Enum(name="invitecodestatus").drop(op.get_bind(), checkfirst=True)
