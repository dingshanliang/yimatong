"""add FK constraint to page_versions page_template_id

Revision ID: f8b1b3069972
Revises: be00cde2b474
Create Date: 2026-06-10 11:57:35.764277

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8b1b3069972'
down_revision: Union[str, None] = 'be00cde2b474'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key("fk_page_versions_page_template_id", 'page_versions', 'page_templates', ['page_template_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint("fk_page_versions_page_template_id", 'page_versions', type_='foreignkey')
