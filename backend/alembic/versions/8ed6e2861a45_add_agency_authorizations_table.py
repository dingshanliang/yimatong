"""add_agency_authorizations_table

Revision ID: 8ed6e2861a45
Revises: 447323f8bd5d
Create Date: 2026-06-03 15:32:42.384091

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8ed6e2861a45'
down_revision: Union[str, None] = '447323f8bd5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agency_authorizations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('agency_tenant_id', sa.Uuid(), nullable=False),
        sa.Column('client_tenant_id', sa.Uuid(), nullable=False),
        sa.Column('scope', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('active', 'revoked', 'expired', name='agencyauthstatus'), nullable=False),
        sa.Column('granted_by', sa.Uuid(), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agency_tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['client_tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['granted_by'], ['accounts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agency_authorizations_agency_tenant_id'), 'agency_authorizations', ['agency_tenant_id'], unique=False)
    op.create_index(op.f('ix_agency_authorizations_client_tenant_id'), 'agency_authorizations', ['client_tenant_id'], unique=False)
    # Composite index for fast lookups by agency
    op.create_index('ix_agency_auth_agency_client', 'agency_authorizations', ['agency_tenant_id', 'client_tenant_id'], unique=False)
    # Partial unique index: only one active authorization per (agency, client) pair
    op.execute(
        "CREATE UNIQUE INDEX uq_agency_auth_active "
        "ON agency_authorizations (agency_tenant_id, client_tenant_id) "
        "WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agency_auth_active")
    op.drop_index('ix_agency_auth_agency_client', table_name='agency_authorizations')
    op.drop_index(op.f('ix_agency_authorizations_client_tenant_id'), table_name='agency_authorizations')
    op.drop_index(op.f('ix_agency_authorizations_agency_tenant_id'), table_name='agency_authorizations')
    op.drop_table('agency_authorizations')
    # Drop the enum type created for this table
    op.execute("DROP TYPE IF EXISTS agencyauthstatus")
