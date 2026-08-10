"""Add frozen provenance validation without scanning existing code items.

Revision ID: b075c9e3f6b8
Revises: af64b8d2e5a7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b075c9e3f6b8"
down_revision: str | None = "af64b8d2e5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_SQL = (
    "(status = 'frozen' AND frozen_from_status IS NOT NULL "
    "AND frozen_from_status IN ('activated', 'bound') AND frozen_at IS NOT NULL "
    "AND freeze_provenance_version IS NOT NULL AND freeze_provenance_version IN (0, 1) "
    "AND ((freeze_provenance_version = 0 AND frozen_by IS NULL AND freeze_reason IS NULL) OR "
    "(freeze_provenance_version = 1 AND NULLIF(trim(frozen_by), '') IS NOT NULL "
    "AND NULLIF(trim(freeze_reason), '') IS NOT NULL))) OR "
    "(status <> 'frozen' AND frozen_from_status IS NULL AND frozen_at IS NULL "
    "AND frozen_by IS NULL AND freeze_reason IS NULL AND freeze_provenance_version IS NULL)"
)
_BACKFILL_INDEX = "ix_code_items_frozen_provenance_backfill"


def _index_valid(name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            "SELECT idx.indisvalid FROM pg_index idx "
            "WHERE idx.indexrelid=to_regclass('public.' || :name)"
        ),
        {"name": name},
    ).scalar()


def _create_backfill_index() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("SET statement_timeout = '10min'")
        if _index_valid(_BACKFILL_INDEX) is False:
            op.execute(f"DROP INDEX CONCURRENTLY public.{_BACKFILL_INDEX}")
        if _index_valid(_BACKFILL_INDEX) is None:
            op.execute(
                f"CREATE INDEX CONCURRENTLY {_BACKFILL_INDEX} "
                "ON public.code_items (id) WHERE status='frozen' AND freeze_provenance_version IS NULL"
            )
        if _index_valid(_BACKFILL_INDEX) is not True:
            raise RuntimeError(f"concurrent index {_BACKFILL_INDEX} is not valid")
        op.execute("RESET lock_timeout")
        op.execute("RESET statement_timeout")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.ix_code_items_frozen_provenance_backfill")
        op.execute("RESET lock_timeout")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(
        "ALTER TABLE public.code_items ADD CONSTRAINT ck_code_items_frozen_provenance "
        f"CHECK ({_CHECK_SQL}) NOT VALID"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _create_backfill_index()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_constraint("ck_code_items_frozen_provenance", "code_items", type_="check")
