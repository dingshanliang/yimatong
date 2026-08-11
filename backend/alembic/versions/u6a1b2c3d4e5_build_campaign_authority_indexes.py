"""Build campaign authority indexes without blocking ordinary writers.

Revision ID: u6a1b2c3d4e5
Revises: u6a0b1c2d3e4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6a1b2c3d4e5"
down_revision: str | None = "u6a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_campaigns_tenant_id_id": (
        True,
        "CREATE UNIQUE INDEX uq_campaigns_tenant_id_id ON public.campaigns USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_campaigns_tenant_id_id ON public.campaigns (tenant_id,id)",
    ),
    "uq_benefit_claims_tenant_id_id": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_claims_tenant_id_id ON public.benefit_claims USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_claims_tenant_id_id "
        "ON public.benefit_claims (tenant_id,id)",
    ),
    "uq_benefit_claims_tenant_idempotent": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_claims_tenant_idempotent ON public.benefit_claims "
        "USING btree (tenant_id, benefit_id, consumer_id, idempotency_key)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_claims_tenant_idempotent "
        "ON public.benefit_claims (tenant_id,benefit_id,consumer_id,idempotency_key)",
    ),
    "uq_benefit_deliveries_tenant_outbox": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_deliveries_tenant_outbox ON public.benefit_deliveries "
        "USING btree (tenant_id, campaign_outbox_id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_deliveries_tenant_outbox "
        "ON public.benefit_deliveries (tenant_id,campaign_outbox_id)",
    ),
    "uq_benefit_deliveries_authority_claim": (
        True,
        "CREATE UNIQUE INDEX uq_benefit_deliveries_authority_claim ON public.benefit_deliveries "
        "USING btree (tenant_id, claim_id) WHERE (campaign_outbox_id IS NOT NULL)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_benefit_deliveries_authority_claim "
        "ON public.benefit_deliveries (tenant_id,claim_id) WHERE campaign_outbox_id IS NOT NULL",
    ),
    "ix_campaigns_tenant_product": (
        False,
        "CREATE INDEX ix_campaigns_tenant_product ON public.campaigns USING btree (tenant_id, product_id)",
        "CREATE INDEX CONCURRENTLY ix_campaigns_tenant_product ON public.campaigns (tenant_id,product_id)",
    ),
    "ix_benefits_tenant_campaign": (
        False,
        "CREATE INDEX ix_benefits_tenant_campaign ON public.benefits USING btree (tenant_id, campaign_id)",
        "CREATE INDEX CONCURRENTLY ix_benefits_tenant_campaign ON public.benefits (tenant_id,campaign_id)",
    ),
    "ix_benefit_claims_tenant_benefit": (
        False,
        "CREATE INDEX ix_benefit_claims_tenant_benefit ON public.benefit_claims USING btree (tenant_id, benefit_id)",
        "CREATE INDEX CONCURRENTLY ix_benefit_claims_tenant_benefit ON public.benefit_claims (tenant_id,benefit_id)",
    ),
    "ix_benefit_claims_tenant_campaign": (
        False,
        "CREATE INDEX ix_benefit_claims_tenant_campaign ON public.benefit_claims USING btree (tenant_id, campaign_id)",
        "CREATE INDEX CONCURRENTLY ix_benefit_claims_tenant_campaign "
        "ON public.benefit_claims (tenant_id,campaign_id)",
    ),
}


def _facts(name: str) -> tuple[bool, bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT index.indisvalid,index.indisunique,pg_get_indexdef(index.indexrelid)
            FROM pg_index AS index
            JOIN pg_class AS relation ON relation.oid=index.indexrelid
            JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname='public' AND relation.relname=:name
            """
        ),
        {"name": name},
    ).one_or_none()
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _preflight() -> None:
    checks = {
        "duplicate campaign tenant identities":
            "SELECT 1 FROM public.campaigns GROUP BY tenant_id,id HAVING count(*)>1 LIMIT 1",
        "duplicate benefit claim tenant identities":
            "SELECT 1 FROM public.benefit_claims GROUP BY tenant_id,id HAVING count(*)>1 LIMIT 1",
        "duplicate tenant claim idempotency facts":
            "SELECT 1 FROM public.benefit_claims GROUP BY tenant_id,benefit_id,consumer_id,idempotency_key "
            "HAVING count(*)>1 LIMIT 1",
        "duplicate authoritative delivery outbox identities":
            "SELECT 1 FROM public.benefit_deliveries WHERE campaign_outbox_id IS NOT NULL "
            "GROUP BY tenant_id,campaign_outbox_id HAVING count(*)>1 LIMIT 1",
        "duplicate authoritative delivery claim identities":
            "SELECT 1 FROM public.benefit_deliveries WHERE campaign_outbox_id IS NOT NULL "
            "GROUP BY tenant_id,claim_id HAVING count(*)>1 LIMIT 1",
    }
    for message, sql in checks.items():
        if op.get_bind().execute(sa.text(sql)).first() is not None:
            raise RuntimeError(message)


def _prepare(name: str, unique: bool, expected: str, create_sql: str) -> None:
    valid, actual_unique, definition = _facts(name)
    if valid and actual_unique is unique and definition == expected:
        return
    if definition is not None and (actual_unique is not unique or definition != expected):
        raise RuntimeError(f"refusing to replace unexpected campaign authority index public.{name}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        try:
            op.execute(create_sql)
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            raise
    valid, actual_unique, definition = _facts(name)
    if not valid or actual_unique is not unique or definition != expected:
        raise RuntimeError(f"campaign authority index public.{name} is not exact and valid")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()
    for name, (unique, expected, create_sql) in _INDEXES.items():
        _prepare(name, unique, expected, create_sql)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name in reversed(tuple(_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
