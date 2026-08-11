"""Attach the code-item authority key and unvalidated scan-event FK.

Revision ID: u4b2f3a4b5c6
Revises: u4a1e2f3a4b5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u4b2f3a4b5c6"
down_revision: str | None = "u4a1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_code_items_tenant_public_id"
_TEMP_INDEX = "uq_code_items_tenant_public_id_downgrade"
_FK = "fk_scan_events_tenant_public_id"


def _scan_partitions() -> tuple[str, ...]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT child.relname FROM pg_inherits inheritance "
            "JOIN pg_class parent ON parent.oid=inheritance.inhparent "
            "JOIN pg_namespace namespace ON namespace.oid=parent.relnamespace "
            "JOIN pg_class child ON child.oid=inheritance.inhrelid "
            "WHERE namespace.nspname='public' AND parent.relname='scan_events' "
            "ORDER BY child.relname"
        )
    )
    return tuple(str(row[0]) for row in rows)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(
        f"ALTER TABLE public.code_items ADD CONSTRAINT {_INDEX} UNIQUE USING INDEX {_INDEX}"
    )
    # PostgreSQL 16 cannot add a NOT VALID FK to a partitioned parent. Attach
    # it online to every leaf first; the final revision validates the leaves
    # and promotes the already-valid family to one parent constraint.
    for partition in _scan_partitions():
        op.execute(
            f"ALTER TABLE public.{partition} ADD CONSTRAINT {_FK} "
            "FOREIGN KEY (tenant_id, public_id) "
            "REFERENCES public.code_items (tenant_id, public_id) NOT VALID"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_TEMP_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_TEMP_INDEX} "
            "ON public.code_items (tenant_id, public_id)"
        )
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for partition in _scan_partitions():
        op.execute(f"ALTER TABLE public.{partition} DROP CONSTRAINT IF EXISTS {_FK}")
    op.execute(f"ALTER TABLE public.code_items DROP CONSTRAINT {_INDEX}")
    op.execute(f"ALTER INDEX public.{_TEMP_INDEX} RENAME TO {_INDEX}")
