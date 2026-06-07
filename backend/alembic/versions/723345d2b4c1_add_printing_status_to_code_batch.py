"""add_printing_status_to_code_batch

CodeBatchStatus uses a String column (not a native ENUM), so new values
(exported, printing, delivered) are purely application-level and require
no database schema change.  This migration exists only as a version
marker to keep the Alembic chain intact.

Revision ID: 723345d2b4c1
Revises: 6610c6bbbddd
Create Date: 2026-06-07 19:32:31.423174

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '723345d2b4c1'
down_revision: Union[str, None] = '6610c6bbbddd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
