"""Build the active consent subject-purpose-policy uniqueness index.

Revision ID: u6h0e1f2a3b4
Revises: u6g0d1e2f3a4
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6h0e1f2a3b4"
down_revision: str | None = "u6g0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_consent_records_tenant_subject_purpose_policy_active"
_EXPECTED = (
    "CREATE UNIQUE INDEX uq_consent_records_tenant_subject_purpose_policy_active "
    "ON public.consent_records USING btree (tenant_id, purpose, visitor_subject_hash, policy_id) "
    "WHERE ((authority_version = 1) AND ((status)::text = 'granted'::text))"
)
_PG_DDL = (
    "CREATE UNIQUE INDEX CONCURRENTLY uq_consent_records_tenant_subject_purpose_policy_active "
    "ON public.consent_records USING btree (tenant_id,purpose,visitor_subject_hash,policy_id) "
    "WHERE authority_version=1 AND status='granted'"
)
_PORTABLE_DDL = (
    "CREATE UNIQUE INDEX uq_consent_records_tenant_subject_purpose_policy_active "
    "ON consent_records (tenant_id,purpose,visitor_subject_hash,policy_id) "
    "WHERE authority_version=1 AND status='granted'"
)


def _assert_no_active_duplicates() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT tenant_id,purpose,visitor_subject_hash,policy_id,count(*) "
            "FROM public.consent_records WHERE authority_version=1 AND status='granted' "
            "GROUP BY tenant_id,purpose,visitor_subject_hash,policy_id HAVING count(*)>1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("duplicate active consent receipts require reviewed remediation")


def _prepare() -> None:
    row = op.get_bind().execute(
        sa.text(
            "SELECT index_catalog.indisvalid,pg_get_indexdef(index_catalog.indexrelid) "
            "FROM pg_index AS index_catalog "
            "JOIN pg_class AS index_class ON index_class.oid=index_catalog.indexrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=index_class.relnamespace "
            "WHERE namespace.nspname='public' AND index_class.relname=:name"
        ),
        {"name": _INDEX},
    ).first()
    if row is not None and row[1] != _EXPECTED:
        raise RuntimeError("same-name active consent subject index has an unexpected definition")
    if row is not None and row[0]:
        return
    if row is not None:
        op.execute(f"DROP INDEX CONCURRENTLY public.{_INDEX}")
    op.execute(_PG_DDL)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.execute(_PORTABLE_DDL)
        return
    _assert_no_active_duplicates()
    with op.get_context().autocommit_block():
        _prepare()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.drop_index(_INDEX)
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
