"""add tenant fields and rls policy

Revision ID: f1099afc4241
Revises: f477814bfdb7
Create Date: 2026-06-07 23:10:26.881772

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1099afc4241"
down_revision: Union[str, None] = "f477814bfdb7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add custom_domain to tenants table
    op.add_column(
        "tenants",
        sa.Column("custom_domain", sa.String(length=255), nullable=True, comment="自定义域名"),
    )

    # 2. Enable RLS on tenants table
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")

    # 3. Create RLS policy for tenants table
    # Policy: users can only see their own tenant; platform admins with bypass_rls can see all
    op.execute("""
    CREATE POLICY tenant_isolation ON tenants
    USING (
        id = current_tenant_id()
        OR (
            current_tenant_id() IS NULL
            AND current_setting('app.bypass_rls', true) = 'true'
        )
    )
    WITH CHECK (
        id = current_tenant_id()
        OR (
            current_tenant_id() IS NULL
            AND current_setting('app.bypass_rls', true) = 'true'
        )
    )
    """)


def downgrade() -> None:
    # 1. Drop RLS policy
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")

    # 2. Drop custom_domain column
    op.drop_column("tenants", "custom_domain")
