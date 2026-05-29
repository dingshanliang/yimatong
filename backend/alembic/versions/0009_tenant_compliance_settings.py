"""add compliance_settings to tenants

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("compliance_settings", sa.JSON, nullable=True, server_default="{}"))


def downgrade() -> None:
    op.drop_column("tenants", "compliance_settings")
