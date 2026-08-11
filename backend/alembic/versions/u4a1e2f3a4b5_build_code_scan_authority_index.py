"""Build the code-item scan authority key without blocking writers.

Revision ID: u4a1e2f3a4b5
Revises: t3c3d2e6f7a8
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u4a1e2f3a4b5"
down_revision: str | None = "t3c3d2e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_code_items_tenant_public_id"
_EXPECTED = (
    "CREATE UNIQUE INDEX uq_code_items_tenant_public_id ON public.code_items "
    "USING btree (tenant_id, public_id)"
)


def _index_facts() -> tuple[bool, bool, str | None]:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            """
            SELECT index.indisvalid, index.indisunique, pg_get_indexdef(index.indexrelid)
            FROM pg_index AS index
            JOIN pg_class AS relation ON relation.oid = index.indexrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname='public' AND relation.relname=:name
            """
        ),
        {"name": _INDEX},
    ).one_or_none()
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _prepare_index() -> None:
    valid, unique, definition = _index_facts()
    if valid and unique and definition == _EXPECTED:
        return
    if definition is not None and (not unique or definition != _EXPECTED):
        raise RuntimeError(
            "refusing to replace unexpected code-item tenant/public-id index"
        )
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_INDEX} ON public.code_items (tenant_id, public_id)"
        )
    valid, unique, definition = _index_facts()
    if not valid or not unique or definition != _EXPECTED:
        raise RuntimeError("code-item tenant/public-id authority index is not valid")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _prepare_index()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
