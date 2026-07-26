"""add unique constraints to brands skus production_batches

Revision ID: ad84a423a60d
Revises: dc03575c9a6c
Create Date: 2026-06-10 11:05:06.764296

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ad84a423a60d'
down_revision: Union[str, None] = 'dc03575c9a6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint('uq_brands_tenant_name', 'brands', ['tenant_id', 'name'])
    op.create_unique_constraint('uq_skus_tenant_product_code', 'skus', ['tenant_id', 'product_id', 'code'])
    op.create_unique_constraint('uq_production_batches_tenant_batch_code', 'production_batches', ['tenant_id', 'batch_code'])


def downgrade() -> None:
    op.drop_constraint('uq_production_batches_tenant_batch_code', 'production_batches', type_='unique')
    op.drop_constraint('uq_skus_tenant_product_code', 'skus', type_='unique')
    op.drop_constraint('uq_brands_tenant_name', 'brands', type_='unique')
