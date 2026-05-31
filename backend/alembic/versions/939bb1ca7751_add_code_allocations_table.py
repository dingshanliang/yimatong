"""add code allocations table

Revision ID: 939bb1ca7751
Revises: 730251dcc816
Create Date: 2026-05-31 10:41:07.696673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '939bb1ca7751'
down_revision: Union[str, None] = '730251dcc816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('code_allocations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('batch_id', sa.Uuid(), nullable=False),
        sa.Column('store_id', sa.Uuid(), nullable=True),
        sa.Column('distributor_id', sa.Uuid(), nullable=True),
        sa.Column('quantity', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('allocated_at', sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(['batch_id'], ['code_batches.id']),
        sa.ForeignKeyConstraint(['distributor_id'], ['distributors.id']),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_code_allocations_tenant_id'), 'code_allocations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_code_allocations_batch_id'), 'code_allocations', ['batch_id'], unique=False)
    op.create_index(op.f('ix_code_allocations_store_id'), 'code_allocations', ['store_id'], unique=False)
    op.create_index(op.f('ix_code_allocations_distributor_id'), 'code_allocations', ['distributor_id'], unique=False)
    op.create_index('ix_code_alloc_batch_store', 'code_allocations', ['batch_id', 'store_id'], unique=False)
    op.create_index('ix_code_alloc_tenant_batch', 'code_allocations', ['tenant_id', 'batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_code_alloc_tenant_batch', table_name='code_allocations')
    op.drop_index('ix_code_alloc_batch_store', table_name='code_allocations')
    op.drop_index(op.f('ix_code_allocations_distributor_id'), table_name='code_allocations')
    op.drop_index(op.f('ix_code_allocations_store_id'), table_name='code_allocations')
    op.drop_index(op.f('ix_code_allocations_batch_id'), table_name='code_allocations')
    op.drop_index(op.f('ix_code_allocations_tenant_id'), table_name='code_allocations')
    op.drop_table('code_allocations')
