"""add_page_version_unique_constraint

Revision ID: b40ccd22d1fe
Revises: ebaa492c1b1e
Create Date: 2026-06-08 21:57:00.399691

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b40ccd22d1fe'
down_revision: Union[str, None] = 'ebaa492c1b1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        'uq_page_versions_template_version',
        'page_versions',
        ['page_template_id', 'version'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_page_versions_template_version',
        'page_versions',
        type_='unique',
    )
