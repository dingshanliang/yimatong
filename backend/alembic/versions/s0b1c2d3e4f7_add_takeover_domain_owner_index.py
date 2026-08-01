"""prevent the same consumer or legacy domain being assigned to two tenants"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s0b1c2d3e4f7"
down_revision: str | None = "s0b1c2d3e4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DOMAIN_OWNER_EXPRESSION = "coalesce(consumer_domain, source_domain)"


def upgrade() -> None:
    op.create_index(
        "uq_takeover_project_domain_owner",
        "takeover_projects",
        [sa.text(DOMAIN_OWNER_EXPRESSION)],
        unique=True,
        postgresql_where=sa.text(f"{DOMAIN_OWNER_EXPRESSION} IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_takeover_project_domain_owner", table_name="takeover_projects")
