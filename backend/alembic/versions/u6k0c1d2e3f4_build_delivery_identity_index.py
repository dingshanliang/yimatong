"""build benefit delivery tenant identity index online

Revision ID: u6k0c1d2e3f4
Revises: u6b5c6d7e8f9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u6k0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u6b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_benefit_deliveries_tenant_id_id": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_deliveries_tenant_id_id "
        "ON public.benefit_deliveries USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_deliveries_tenant_id_id "
        "ON public.benefit_deliveries (tenant_id,id)",
    ),
    "ix_campaign_delivery_legacy_retry": (
        False,
        "CREATE INDEX ix_campaign_delivery_legacy_retry ON public.benefit_deliveries "
        "USING btree (tenant_id, next_retry_at, id) "
        "WHERE ((campaign_outbox_id IS NULL) AND ((status)::text = 'pending'::text))",
        "CREATE INDEX CONCURRENTLY ix_campaign_delivery_legacy_retry ON public.benefit_deliveries "
        "(tenant_id,next_retry_at,id) WHERE campaign_outbox_id IS NULL AND status='pending'",
    ),
    "uq_benefit_deliveries_tenant_id_id_downgrade": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_deliveries_tenant_id_id_downgrade "
        "ON public.benefit_deliveries USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_deliveries_tenant_id_id_downgrade "
        "ON public.benefit_deliveries (tenant_id,id)",
    ),
}


def _facts(name: str) -> tuple[bool, bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=:name"
        ),
        {"name": name},
    ).one_or_none()
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _prepare(name: str, expected_unique: bool, expected: str, create_sql: str) -> None:
    valid, unique, definition = _facts(name)
    if valid and unique is expected_unique and definition == expected:
        return
    if definition is not None and (unique is not expected_unique or definition != expected):
        raise RuntimeError(f"refusing to replace unexpected delivery authority index public.{name}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        try:
            op.execute(create_sql)
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            raise
    valid, unique, definition = _facts(name)
    if not valid or unique is not expected_unique or definition != expected:
        raise RuntimeError(f"delivery authority index public.{name} is not exact and valid")


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("delivery authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downstream_fact_preflight() -> None:
    bind = op.get_bind()
    if _destination_is_below("u6b5c6d7e8f9") and bind.execute(
        sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("u6k0 downgrade blocked: bound benefit claims are immutable facts")
    if _destination_is_below("u7c0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM public.risk_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6k0 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM public.diversion_observations)+"
            "(SELECT count(*) FROM public.diversion_action_receipts)+"
            "(SELECT count(*) FROM public.diversion_evidence)+"
            "(SELECT count(*) FROM public.diversion_investigation_history)"
        )
    ).scalar_one():
        raise RuntimeError("u6k0 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4") and bind.execute(
        sa.text("SELECT count(*) FROM public.channel_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6k0 downgrade blocked: channel action receipts are immutable facts")


def upgrade() -> None:
    for name, (unique, expected, create_sql) in _INDEXES.items():
        _prepare(name, unique, expected, create_sql)


def downgrade() -> None:
    _downstream_fact_preflight()
    with op.get_context().autocommit_block():
        for name in reversed(tuple(_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
