"""build WeCom contact tenant identity index online

Revision ID: u6l1d2e3f4a5
Revises: u6l0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6l1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u6l0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_wecom_external_contacts_tenant_id_id_u6l": (
        True,
        "CREATE UNIQUE INDEX uq_wecom_external_contacts_tenant_id_id_u6l "
        "ON public.wecom_external_contacts USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_wecom_external_contacts_tenant_id_id_u6l "
        "ON public.wecom_external_contacts (tenant_id,id)",
    ),
    "ix_wecom_callback_receipts_tenant_contact": (
        False,
        "CREATE INDEX ix_wecom_callback_receipts_tenant_contact "
        "ON public.wecom_callback_receipts USING btree (tenant_id, contact_id)",
        "CREATE INDEX CONCURRENTLY ix_wecom_callback_receipts_tenant_contact "
        "ON public.wecom_callback_receipts (tenant_id,contact_id)",
    ),
}
_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "5s"


def _facts(name: str) -> tuple[bool, bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": name},
        )
        .one_or_none()
    )
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _preflight_receipts() -> None:
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM public.wecom_callback_receipts r LEFT JOIN public.wecom_external_contacts c "
                "ON c.tenant_id=r.tenant_id AND c.id=CASE "
                "WHEN r.contact_id IS NOT NULL THEN r.contact_id "
                "WHEN jsonb_typeof(r.result::jsonb->'contact_id')='string' "
                "AND (r.result->>'contact_id') ~* "
                "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
                "THEN (r.result->>'contact_id')::uuid ELSE NULL END WHERE c.id IS NULL"
            )
        )
        .scalar_one()
    )
    if invalid:
        raise RuntimeError(
            "cannot bind verified WeCom receipts without an authoritative same-tenant contact projection"
        )


def _prepare(name: str, expected_unique: bool, expected: str, create_sql: str) -> None:
    valid, unique, definition = _facts(name)
    if valid and unique is expected_unique and definition == expected:
        return
    if definition is not None and (unique is not expected_unique or definition != expected):
        raise RuntimeError(f"refusing unexpected WeCom authority index public.{name}")
    if definition is not None:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
    op.execute(create_sql)
    valid, unique, definition = _facts(name)
    if not valid or unique is not expected_unique or definition != expected:
        raise RuntimeError(f"WeCom authority index public.{name} is not exact and valid")


def _wait_for_preexisting_snapshots() -> None:
    """Fail before CIC writes catalog state if an older snapshot would make it wait."""

    op.execute(
        "SELECT pg_sleep(10) WHERE EXISTS ("
        "SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
        "AND pid<>pg_backend_pid() AND backend_xmin IS NOT NULL)"
    )


def upgrade() -> None:
    _preflight_receipts()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout='{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout='{_STATEMENT_TIMEOUT}'")
        try:
            _wait_for_preexisting_snapshots()
            for name, (unique, expected, create_sql) in _INDEXES.items():
                try:
                    _prepare(name, unique, expected, create_sql)
                except BaseException:
                    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
                    raise
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")


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
