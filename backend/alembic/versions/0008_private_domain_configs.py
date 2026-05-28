"""private_domain_configs 表

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "private_domain_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("config_type", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_private_domain_configs_tenant_id",
        "private_domain_configs",
        ["tenant_id"],
    )

    # RLS
    op.execute("""
        ALTER TABLE private_domain_configs ENABLE ROW LEVEL SECURITY;
        CREATE POLICY private_domain_configs_tenant_isolation ON private_domain_configs
            USING (tenant_id = current_tenant_id());
    """)


def downgrade() -> None:
    op.drop_table("private_domain_configs")
