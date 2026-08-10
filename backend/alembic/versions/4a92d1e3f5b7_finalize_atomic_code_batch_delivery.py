"""Finalize the atomic code-batch delivery rollout after old writers drain.

Revision ID: 4a92d1e3f5b7
Revises: 3f91c0d2e4a6
Create Date: 2026-08-11

The expand revision keeps pre-contract runtime SQL operational while the old
application fleet drains.  This finalize revision takes a bounded writer
fence across the authoritative parent/child tables before publishing the
singleton marker observed by the expand guards.  A timeout rolls the whole
transaction back, leaving expand behavior active and making retry safe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4a92d1e3f5b7"
down_revision: str | None = "3f91c0d2e4a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLLOUT_STATE_TABLE = "code_delivery_contract_rollout_state"


def _lock_delivery_writers() -> None:
    # Deployment must first drain old application processes.  SHARE ROW
    # EXCLUSIVE then prevents old and new batch/item/export writers from
    # crossing the marker publication boundary.  The bounded timeout fails the
    # revision without publishing a partial finalize state if a writer remains
    # active.  Keep this single statement in parent -> child -> artifact order.
    op.execute(
        "LOCK TABLE public.code_batches, public.code_items, public.export_logs "
        "IN SHARE ROW EXCLUSIVE MODE"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _lock_delivery_writers()
    op.execute(
        f"INSERT INTO public.{_ROLLOUT_STATE_TABLE} "
        "(id, phase, finalized_at, finalized_by) "
        "VALUES (1, 'finalized', CURRENT_TIMESTAMP, CURRENT_USER)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _lock_delivery_writers()
    op.execute(f"DELETE FROM public.{_ROLLOUT_STATE_TABLE} WHERE id = 1 AND phase = 'finalized'")
