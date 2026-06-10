"""add unique constraint to code_batches tenant_batch_code

Revision ID: be00cde2b474
Revises: ad84a423a60d
Create Date: 2026-06-10 11:19:22.705944

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'be00cde2b474'
down_revision: Union[str, None] = 'ad84a423a60d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint('uq_code_batches_tenant_batch_code', 'code_batches', ['tenant_id', 'batch_code'])


def downgrade() -> None:
    op.drop_constraint('uq_code_batches_tenant_batch_code', 'code_batches', type_='unique')
