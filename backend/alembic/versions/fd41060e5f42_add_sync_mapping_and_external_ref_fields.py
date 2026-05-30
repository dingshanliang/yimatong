"""add_sync_mapping_and_external_ref_fields

Revision ID: fd41060e5f42
Revises: 199331c89363
Create Date: 2026-05-30 11:50:49.612503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fd41060e5f42'
down_revision: Union[str, None] = '199331c89363'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SyncMapping 表
    op.create_table('sync_mappings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('local_entity_type', sa.String(length=50), nullable=False),
    sa.Column('local_entity_id', sa.Uuid(), nullable=False),
    sa.Column('source_system', sa.String(length=50), nullable=False),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('external_phone_hash', sa.String(length=64), nullable=True),
    sa.Column('sync_direction', sa.String(length=20), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'source_system', 'external_id', name='uq_sync_mappings_tenant_source_ext')
    )
    op.create_index('ix_sync_mappings_entity', 'sync_mappings', ['tenant_id', 'local_entity_type', 'local_entity_id'], unique=False)
    op.create_index(op.f('ix_sync_mappings_tenant_id'), 'sync_mappings', ['tenant_id'], unique=False)

    # Brand: external_id + source_system
    op.add_column('brands', sa.Column('external_id', sa.String(length=100), nullable=True))
    op.add_column('brands', sa.Column('source_system', sa.String(length=50), nullable=True))

    # Product: external_id + source_system
    op.add_column('products', sa.Column('external_id', sa.String(length=100), nullable=True))
    op.add_column('products', sa.Column('source_system', sa.String(length=50), nullable=True))

    # SKU: external_id + source_system
    op.add_column('skus', sa.Column('external_id', sa.String(length=100), nullable=True))
    op.add_column('skus', sa.Column('source_system', sa.String(length=50), nullable=True))

    # ProductionBatch: external_id + source_system
    op.add_column('production_batches', sa.Column('external_id', sa.String(length=100), nullable=True))
    op.add_column('production_batches', sa.Column('source_system', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('production_batches', 'source_system')
    op.drop_column('production_batches', 'external_id')
    op.drop_column('skus', 'source_system')
    op.drop_column('skus', 'external_id')
    op.drop_column('products', 'source_system')
    op.drop_column('products', 'external_id')
    op.drop_column('brands', 'source_system')
    op.drop_column('brands', 'external_id')

    op.drop_index('ix_sync_mappings_entity', table_name='sync_mappings')
    op.drop_index(op.f('ix_sync_mappings_tenant_id'), table_name='sync_mappings')
    op.drop_table('sync_mappings')
