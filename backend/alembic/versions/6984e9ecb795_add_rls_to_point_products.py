"""add RLS to point_products

Revision ID: 6984e9ecb795
Revises: b40ccd22d1fe
Create Date: 2026-06-09 09:52:27.337613

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6984e9ecb795'
down_revision: Union[str, None] = 'b40ccd22d1fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RLS_POLICY_SQL = """
CREATE POLICY tenant_isolation ON {table}
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


def upgrade() -> None:
    op.execute("ALTER TABLE point_products ENABLE ROW LEVEL SECURITY")
    op.execute(RLS_POLICY_SQL.format(table="point_products"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON point_products")
    op.execute("ALTER TABLE point_products DISABLE ROW LEVEL SECURITY")
