"""allow standalone benefits

Revision ID: e3f4a5b6c7d8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-01 15:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("benefits", "campaign_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM benefits WHERE campaign_id IS NULL")
    op.alter_column("benefits", "campaign_id", existing_type=sa.Uuid(), nullable=False)
