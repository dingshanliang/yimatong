"""build external order ledger indexes online

Revision ID: u8a1d2e3f4a5
Revises: u8a0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8a1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u8a0c1d2e3f4"
branch_labels = None
depends_on = None

_INDEXES = {
    "uq_external_orders_tenant_id_id_u8a": (
        True,
        "CREATE UNIQUE INDEX uq_external_orders_tenant_id_id_u8a "
        "ON public.external_orders USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_external_orders_tenant_id_id_u8a "
        "ON public.external_orders(tenant_id,id)",
    ),
    "uq_external_order_receipts_idem_u8a": (
        True,
        "CREATE UNIQUE INDEX uq_external_order_receipts_idem_u8a "
        "ON public.external_order_value_receipts USING btree (tenant_id, source_system, idempotency_key)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_external_order_receipts_idem_u8a "
        "ON public.external_order_value_receipts(tenant_id,source_system,idempotency_key)",
    ),
    "uq_external_order_receipts_tenant_id_u8a": (
        True,
        "CREATE UNIQUE INDEX uq_external_order_receipts_tenant_id_u8a "
        "ON public.external_order_value_receipts USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_external_order_receipts_tenant_id_u8a "
        "ON public.external_order_value_receipts(tenant_id,id)",
    ),
    "uq_external_order_events_sequence_u8a": (
        True,
        "CREATE UNIQUE INDEX uq_external_order_events_sequence_u8a "
        "ON public.external_order_value_events USING btree (tenant_id, order_id, sequence_no)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_external_order_events_sequence_u8a "
        "ON public.external_order_value_events(tenant_id,order_id,sequence_no)",
    ),
    "uq_external_order_events_tenant_id_u8a": (
        True,
        "CREATE UNIQUE INDEX uq_external_order_events_tenant_id_u8a "
        "ON public.external_order_value_events USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_external_order_events_tenant_id_u8a "
        "ON public.external_order_value_events(tenant_id,id)",
    ),
    "ix_external_order_events_identity_u8a": (
        False,
        "CREATE INDEX ix_external_order_events_identity_u8a "
        "ON public.external_order_value_events USING btree "
        "(tenant_id, source_system, external_order_id, sequence_no)",
        "CREATE INDEX CONCURRENTLY ix_external_order_events_identity_u8a "
        "ON public.external_order_value_events(tenant_id,source_system,external_order_id,sequence_no)",
    ),
}
_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "5s"


def _facts(name: str) -> tuple[bool, bool, bool, bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,i.indisready,i.indislive,i.indisunique,pg_get_indexdef(i.indexrelid) "
                "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": name},
        )
        .one_or_none()
    )
    if row is None:
        return False, False, False, False, None
    return bool(row[0]), bool(row[1]), bool(row[2]), bool(row[3]), str(row[4])


def _is_exact(name: str, expected_unique: bool, expected_definition: str) -> bool:
    valid, ready, live, unique, definition = _facts(name)
    return valid and ready and live and unique is expected_unique and definition == expected_definition


def _preflight() -> None:
    for name, (expected_unique, expected_definition, _) in _INDEXES.items():
        valid, ready, live, unique, definition = _facts(name)
        if definition is not None and (unique is not expected_unique or definition != expected_definition):
            raise RuntimeError(f"refusing unexpected external order ledger index public.{name}")
        if definition is not None and valid and (not ready or not live):
            raise RuntimeError(f"refusing inconsistent external order ledger index public.{name}")


def _prepare(name: str, expected_unique: bool, expected_definition: str, create_sql: str) -> None:
    if _is_exact(name, expected_unique, expected_definition):
        return
    if _facts(name)[4] is not None:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
    op.execute(create_sql)
    if not _is_exact(name, expected_unique, expected_definition):
        raise RuntimeError(f"external order ledger index public.{name} is not exact and valid")


def upgrade() -> None:
    _preflight()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout='{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout='{_STATEMENT_TIMEOUT}'")
        coordination_locked = False
        try:
            # Every external_orders writer installed by u8a0 holds the shared
            # xact variant. This bounded exclusive session lock therefore waits
            # for preexisting target writers before CIC publishes a shell, then
            # prevents late target writers until every index is valid.
            op.execute(
                "SELECT pg_advisory_lock(hashtextextended('u8a:external_orders:index-build',0))"
            )
            coordination_locked = True
            for name, (expected_unique, expected_definition, create_sql) in _INDEXES.items():
                _prepare(name, expected_unique, expected_definition, create_sql)
        finally:
            try:
                op.execute("SET statement_timeout=DEFAULT")
                op.execute("SET lock_timeout=DEFAULT")
            finally:
                if coordination_locked:
                    op.execute(
                        "SELECT pg_advisory_unlock(hashtextextended('u8a:external_orders:index-build',0))"
                    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout='{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout='{_STATEMENT_TIMEOUT}'")
        try:
            for name in reversed(tuple(_INDEXES)):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")
