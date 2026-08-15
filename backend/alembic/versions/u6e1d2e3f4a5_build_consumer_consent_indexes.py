"""build consumer consent authority indexes

Revision ID: u6e1d2e3f4a5
Revises: u6e0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6e1d2e3f4a5"
down_revision: str | None = "u6e0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_consumer_profiles_tenant_id": (
        "CREATE UNIQUE INDEX uq_consumer_profiles_tenant_id ON public.consumer_profiles USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_consumer_profiles_tenant_id "
        "ON public.consumer_profiles USING btree (tenant_id,id)",
        "CREATE UNIQUE INDEX uq_consumer_profiles_tenant_id ON consumer_profiles (tenant_id,id)",
    ),
    "uq_consent_records_tenant_id": (
        "CREATE UNIQUE INDEX uq_consent_records_tenant_id ON public.consent_records USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_consent_records_tenant_id "
        "ON public.consent_records USING btree (tenant_id,id)",
        "CREATE UNIQUE INDEX uq_consent_records_tenant_id ON consent_records (tenant_id,id)",
    ),
    "uq_consent_records_tenant_idempotency": (
        "CREATE UNIQUE INDEX uq_consent_records_tenant_idempotency ON public.consent_records USING btree "
        "(tenant_id, idempotency_key) WHERE (authority_version = 1)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_consent_records_tenant_idempotency "
        "ON public.consent_records USING btree (tenant_id,idempotency_key) WHERE authority_version=1",
        "CREATE UNIQUE INDEX uq_consent_records_tenant_idempotency "
        "ON consent_records (tenant_id,idempotency_key) WHERE authority_version=1",
    ),
    "ix_consent_records_tenant_subject": (
        "CREATE INDEX ix_consent_records_tenant_subject ON public.consent_records USING btree "
        "(tenant_id, purpose, visitor_subject_hash)",
        "CREATE INDEX CONCURRENTLY ix_consent_records_tenant_subject "
        "ON public.consent_records USING btree (tenant_id,purpose,visitor_subject_hash)",
        "CREATE INDEX ix_consent_records_tenant_subject "
        "ON consent_records (tenant_id,purpose,visitor_subject_hash)",
    ),
    "ix_consumer_profiles_tenant_lead_consent": (
        "CREATE INDEX ix_consumer_profiles_tenant_lead_consent ON public.consumer_profiles USING btree "
        "(tenant_id, lead_consent_id)",
        "CREATE INDEX CONCURRENTLY ix_consumer_profiles_tenant_lead_consent "
        "ON public.consumer_profiles USING btree (tenant_id,lead_consent_id)",
        "CREATE INDEX ix_consumer_profiles_tenant_lead_consent "
        "ON consumer_profiles (tenant_id,lead_consent_id)",
    ),
}


def _prepare(name: str, expected: str, ddl: str) -> None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": name},
        )
        .first()
    )
    if row and row[1] != expected:
        raise RuntimeError(f"same-name consent index has an unexpected definition: {name}")
    if row and row[0]:
        return
    if row:
        op.execute(f"DROP INDEX CONCURRENTLY public.{name}")
    op.execute(ddl)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        for _name, (_expected, _pg, portable) in _INDEXES.items():
            op.execute(portable)
        return
    with op.get_context().autocommit_block():
        for name, (expected, ddl, _portable) in _INDEXES.items():
            _prepare(name, expected, ddl)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        for name in reversed(tuple(_INDEXES)):
            op.drop_index(name)
        return
    with op.get_context().autocommit_block():
        for name in reversed(tuple(_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
