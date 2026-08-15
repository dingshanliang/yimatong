"""expand export ledger authority metadata

Revision ID: u8c0f1a2b3c4
Revises: u8b2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "u8c0f1a2b3c4"
down_revision: str | Sequence[str] | None = "u8b2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Metadata-only nullable expansion. No table rewrite, data scan, index build,
    # or validation shares this short ACCESS EXCLUSIVE lock transaction.
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")
    op.add_column("export_logs", sa.Column("auth_session_id", sa.Uuid(), nullable=True))
    op.add_column("export_logs", sa.Column("reason", sa.String(500), nullable=True))
    op.add_column(
        "export_logs",
        sa.Column("scope_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("export_logs", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("export_logs", sa.Column("payload_digest", sa.String(64), nullable=True))
    op.add_column("export_logs", sa.Column("content_type", sa.String(127), nullable=True))
    op.add_column("export_logs", sa.Column("authority_version", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    for column in (
        "authority_version",
        "content_type",
        "payload_digest",
        "idempotency_key",
        "scope_snapshot",
        "reason",
        "auth_session_id",
    ):
        op.drop_column("export_logs", column)
