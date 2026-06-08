"""add FK constraints, email unique, fix scope type

Revision ID: f477814bfdb7
Revises: 244bd763a13d
Create Date: 2026-06-07 22:02:02.602593

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f477814bfdb7'
down_revision: Union[str, None] = '244bd763a13d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add FK constraints
    op.create_foreign_key('fk_accounts_tenant_id', 'accounts', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_roles_tenant_id', 'roles', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_permissions_tenant_id', 'permissions', 'tenants', ['tenant_id'], ['id'])
    
    # Add unique constraint for email per tenant
    op.create_unique_constraint('uq_account_tenant_email', 'accounts', ['tenant_id', 'email'])


def downgrade() -> None:
    op.drop_constraint('uq_account_tenant_email', 'accounts', type_='unique')
    op.drop_constraint('fk_permissions_tenant_id', 'permissions', type_='foreignkey')
    op.drop_constraint('fk_roles_tenant_id', 'roles', type_='foreignkey')
    op.drop_constraint('fk_accounts_tenant_id', 'accounts', type_='foreignkey')
