"""build webhook outbox indexes

Revision ID: u8d2a3b4c5d6
Revises: u8d1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u8d2a3b4c5d6"
down_revision: str | Sequence[str] | None = "u8d1f2a3b4c5"
branch_labels = None
depends_on = None

# Concurrent index DDL commits independently of Alembic's revision transaction.
# Exact catalog definitions make a partially completed upgrade safely resumable
# without replacing a same-name operator-owned index.
_INDEXES = {
    "uq_webhook_endpoints_tenant_id_id_idx": (
        True,
        "CREATE UNIQUE INDEX uq_webhook_endpoints_tenant_id_id_idx "
        "ON public.webhook_endpoints USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_webhook_endpoints_tenant_id_id_idx "
        "ON public.webhook_endpoints (tenant_id,id)",
    ),
    "uq_webhook_domain_events_tenant_id_id_idx": (
        True,
        "CREATE UNIQUE INDEX uq_webhook_domain_events_tenant_id_id_idx "
        "ON public.webhook_domain_events USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_webhook_domain_events_tenant_id_id_idx "
        "ON public.webhook_domain_events (tenant_id,id)",
    ),
    "ix_webhook_domain_events_tenant_created": (
        False,
        "CREATE INDEX ix_webhook_domain_events_tenant_created "
        "ON public.webhook_domain_events USING btree (tenant_id, created_at, id)",
        "CREATE INDEX CONCURRENTLY ix_webhook_domain_events_tenant_created "
        "ON public.webhook_domain_events (tenant_id,created_at,id)",
    ),
    "ix_webhook_domain_events_pending": (
        False,
        "CREATE INDEX ix_webhook_domain_events_pending "
        "ON public.webhook_domain_events USING btree (created_at, id) WHERE (expanded_at IS NULL)",
        "CREATE INDEX CONCURRENTLY ix_webhook_domain_events_pending "
        "ON public.webhook_domain_events (created_at,id) WHERE expanded_at IS NULL",
    ),
    "uq_webhook_deliveries_domain_endpoint": (
        True,
        "CREATE UNIQUE INDEX uq_webhook_deliveries_domain_endpoint "
        "ON public.webhook_deliveries USING btree (tenant_id, domain_event_id, endpoint_id) "
        "WHERE (domain_event_id IS NOT NULL)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_webhook_deliveries_domain_endpoint "
        "ON public.webhook_deliveries (tenant_id,domain_event_id,endpoint_id) "
        "WHERE domain_event_id IS NOT NULL",
    ),
    "ix_webhook_deliveries_due": (
        False,
        "CREATE INDEX ix_webhook_deliveries_due "
        "ON public.webhook_deliveries USING btree (status, next_retry_at, lease_expires_at, id)",
        "CREATE INDEX CONCURRENTLY ix_webhook_deliveries_due "
        "ON public.webhook_deliveries (status,next_retry_at,lease_expires_at,id)",
    ),
}


def _facts(name: str) -> tuple[bool, bool, bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            "SELECT i.indisvalid,i.indisready,i.indisunique,pg_get_indexdef(i.indexrelid) "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=:name"
        ),
        {"name": name},
    ).one_or_none()
    if row is None:
        return False, False, False, None
    return bool(row[0]), bool(row[1]), bool(row[2]), str(row[3])


def _concurrent_ddl(statement: str) -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        op.execute("SET statement_timeout='120s'")
        try:
            op.execute(statement)
        finally:
            op.execute("SET lock_timeout=DEFAULT")
            op.execute("SET statement_timeout=DEFAULT")


def _ensure(name: str, expected_unique: bool, expected_definition: str, create_sql: str) -> None:
    valid, ready, unique, definition = _facts(name)
    if definition is not None and (unique != expected_unique or definition != expected_definition):
        raise RuntimeError(f"refusing to replace unexpected webhook outbox index public.{name}")
    if definition is not None and valid and ready:
        return
    if definition is not None:
        _concurrent_ddl(f"DROP INDEX CONCURRENTLY public.{name}")
    _concurrent_ddl(create_sql)
    valid, ready, unique, definition = _facts(name)
    if not valid or not ready or unique != expected_unique or definition != expected_definition:
        raise RuntimeError(f"webhook outbox index public.{name} is not exact, ready, and valid")


def _drop_owned(name: str, expected_unique: bool, expected_definition: str) -> None:
    _valid, _ready, unique, definition = _facts(name)
    if definition is None:
        return
    if unique != expected_unique or definition != expected_definition:
        raise RuntimeError(f"refusing to drop unexpected webhook outbox index public.{name}")
    _concurrent_ddl(f"DROP INDEX CONCURRENTLY public.{name}")


def upgrade() -> None:
    for name, (unique, expected_definition, create_sql) in _INDEXES.items():
        _ensure(name, unique, expected_definition, create_sql)


def downgrade() -> None:
    for name, (unique, expected_definition, _create_sql) in reversed(tuple(_INDEXES.items())):
        _drop_owned(name, unique, expected_definition)
