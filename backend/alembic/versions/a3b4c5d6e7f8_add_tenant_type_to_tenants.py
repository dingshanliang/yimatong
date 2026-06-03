"""add tenant_type to tenants

Revision ID: a3b4c5d6e7f8
Revises: 2ba57df9bc9f
Create Date: 2026-06-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = '2ba57df9bc9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type first (PostgreSQL)
    tenanttype = sa.Enum('brand', 'agency', 'regional_org', 'platform', name='tenanttype')
    tenanttype.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'tenants',
        sa.Column(
            'tenant_type',
            sa.Enum('brand', 'agency', 'regional_org', 'platform', name='tenanttype'),
            nullable=False,
            server_default='brand',
        ),
    )


def downgrade() -> None:
    op.drop_column('tenants', 'tenant_type')
    # Drop enum type
    tenanttype = sa.Enum('brand', 'agency', 'regional_org', 'platform', name='tenanttype')
    tenanttype.drop(op.get_bind(), checkfirst=True)
