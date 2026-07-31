"""add tenant-scoped launch release gate records

Revision ID: r9a0b1c2d3e4
Revises: c3ba056156e0
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "r9a0b1c2d3e4"
down_revision: str | None = "c3ba056156e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "launch_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("page_template_id", sa.Uuid(), nullable=False),
        sa.Column("page_version_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("code_batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("brand_confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("brand_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("brand_confirmation_digest", sa.String(length=64), nullable=True),
        sa.Column("launched_by", sa.Uuid(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("suspended_by", sa.Uuid(), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspension_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["page_template_id"], ["page_templates.id"]),
        sa.ForeignKeyConstraint(["page_version_id"], ["page_versions.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["code_batch_id"], ["code_batches.id"]),
        sa.ForeignKeyConstraint(["brand_confirmed_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["launched_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["suspended_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_launch_releases_tenant_idempotency"),
    )
    op.create_index("ix_launch_releases_tenant_id", "launch_releases", ["tenant_id"], unique=False)
    op.create_index("ix_launch_releases_page_template_id", "launch_releases", ["page_template_id"], unique=False)
    op.create_index("ix_launch_releases_page_version_id", "launch_releases", ["page_version_id"], unique=False)
    op.create_index("ix_launch_releases_campaign_id", "launch_releases", ["campaign_id"], unique=False)
    op.create_index("ix_launch_releases_code_batch_id", "launch_releases", ["code_batch_id"], unique=False)
    op.create_index("ix_launch_releases_tenant_status", "launch_releases", ["tenant_id", "status"], unique=False)
    op.execute("ALTER TABLE launch_releases ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON launch_releases
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON launch_releases")
    op.execute("ALTER TABLE launch_releases DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_launch_releases_tenant_status", table_name="launch_releases")
    op.drop_index("ix_launch_releases_code_batch_id", table_name="launch_releases")
    op.drop_index("ix_launch_releases_campaign_id", table_name="launch_releases")
    op.drop_index("ix_launch_releases_page_version_id", table_name="launch_releases")
    op.drop_index("ix_launch_releases_page_template_id", table_name="launch_releases")
    op.drop_index("ix_launch_releases_tenant_id", table_name="launch_releases")
    op.drop_table("launch_releases")
