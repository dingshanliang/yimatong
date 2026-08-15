"""build WeCom member-scoped contact indexes online

Revision ID: u6l3f4a5b6c7
Revises: u6l2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6l3f4a5b6c7"
down_revision: str | Sequence[str] | None = "u6l2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_wecom_external_contacts_member_source_u6l": (
        True,
        "CREATE UNIQUE INDEX uq_wecom_external_contacts_member_source_u6l "
        "ON public.wecom_external_contacts USING btree "
        "(tenant_id, connector_id, user_id, external_userid)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_wecom_external_contacts_member_source_u6l "
        "ON public.wecom_external_contacts (tenant_id,connector_id,user_id,external_userid)",
    ),
    "ix_wecom_external_contacts_member_order_u6l": (
        False,
        "CREATE INDEX ix_wecom_external_contacts_member_order_u6l "
        "ON public.wecom_external_contacts USING btree "
        "(tenant_id, connector_id, user_id, external_userid, event_time, event_sequence)",
        "CREATE INDEX CONCURRENTLY ix_wecom_external_contacts_member_order_u6l "
        "ON public.wecom_external_contacts "
        "(tenant_id,connector_id,user_id,external_userid,event_time,event_sequence)",
    ),
    "ix_wecom_callback_receipts_member_order_u6l": (
        False,
        "CREATE INDEX ix_wecom_callback_receipts_member_order_u6l "
        "ON public.wecom_callback_receipts USING btree "
        "(tenant_id, connector_id, user_id, external_userid, event_time, event_sequence)",
        "CREATE INDEX CONCURRENTLY ix_wecom_callback_receipts_member_order_u6l "
        "ON public.wecom_callback_receipts "
        "(tenant_id,connector_id,user_id,external_userid,event_time,event_sequence)",
    ),
}
_LOCK_TIMEOUT = "5s"
_STATEMENT_TIMEOUT = "5s"
_FUNCTION = "apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"
_LEGACY_FUNCTION = (
    "apply_verified_wecom_contact_event_u6l3(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"
)


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


def _prepare(name: str, expected_unique: bool, expected: str, create_sql: str) -> None:
    valid, unique, definition = _facts(name)
    if valid and unique is expected_unique and definition == expected:
        return
    if definition is not None and (unique is not expected_unique or definition != expected):
        raise RuntimeError(f"refusing unexpected WeCom member authority index public.{name}")
    if definition is not None:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
    op.execute(create_sql)
    valid, unique, definition = _facts(name)
    if not valid or unique is not expected_unique or definition != expected:
        raise RuntimeError(f"WeCom member authority index public.{name} is not exact and valid")


def _preflight_index_names() -> None:
    for name, (expected_unique, expected, _) in _INDEXES.items():
        _, unique, definition = _facts(name)
        if definition is not None and (unique is not expected_unique or definition != expected):
            raise RuntimeError(f"refusing unexpected WeCom member authority index public.{name}")


def _preflight_member_facts() -> None:
    bind = op.get_bind()
    unresolved_markers = bind.execute(
        sa.text("SELECT count(*) FROM public.wecom_member_recovery_markers WHERE resolution_state='unresolved'")
    ).scalar_one()
    invalid_receipts = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.wecom_callback_receipts r "
            "LEFT JOIN public.wecom_external_contacts c ON c.tenant_id=r.tenant_id AND c.id=r.contact_id "
            "LEFT JOIN public.wecom_contact_ways w ON w.tenant_id=r.tenant_id AND w.connector_id=r.connector_id "
            "AND (w.state=r.state OR (r.change_type IN ('del_external_contact','del_follow_user') "
            "AND r.state IS NULL AND w.id=c.contact_way_id)) "
            "AND w.status='active' AND jsonb_typeof(w.user_ids::jsonb)='array' "
            "AND w.user_ids::jsonb @> jsonb_build_array(r.user_id) "
            "WHERE r.user_id IS NULL OR NULLIF(trim(r.user_id),'') IS NULL OR w.id IS NULL"
        )
    ).scalar_one()
    ambiguous_projection = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.wecom_external_contacts c "
            "WHERE c.verification_source IN ('confirmed_callback','pending_callback','termination_callback') "
            "AND (c.user_id IS NULL OR NULLIF(trim(c.user_id),'') IS NULL OR NOT EXISTS ("
            "SELECT 1 FROM public.wecom_contact_ways w WHERE w.tenant_id=c.tenant_id "
            "AND w.connector_id=c.connector_id AND w.state=c.state AND w.status='active' "
            "AND jsonb_typeof(w.user_ids::jsonb)='array' AND w.user_ids::jsonb @> jsonb_build_array(c.user_id)))"
        )
    ).scalar_one()
    duplicate_projection = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT tenant_id,connector_id,user_id,external_userid "
            "FROM public.wecom_external_contacts GROUP BY tenant_id,connector_id,user_id,external_userid "
            "HAVING count(*)>1) duplicated"
        )
    ).scalar_one()
    if unresolved_markers or invalid_receipts or ambiguous_projection or duplicate_projection:
        raise RuntimeError("cannot bind verified WeCom facts to one exact serving member")


def _wait_for_preexisting_snapshots() -> None:
    op.execute(
        "SELECT pg_sleep(10) WHERE EXISTS ("
        "SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
        "AND pid<>pg_backend_pid() AND backend_xmin IS NOT NULL)"
    )


def _preserve_legacy_function() -> None:
    op.execute(r"""
      DO $body$ DECLARE definition text;
      BEGIN
        definition:=pg_get_functiondef(
          'public.apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)'
          ::regprocedure);
        definition:=replace(definition,'public.apply_verified_wecom_contact_event(',
          'public.apply_verified_wecom_contact_event_u6l3(');
        IF definition NOT LIKE '%public.apply_verified_wecom_contact_event_u6l3(%' THEN
          RAISE EXCEPTION 'could not preserve the exact pre-member WeCom authority';
        END IF;
        EXECUTE definition;
      END $body$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_LEGACY_FUNCTION} FROM PUBLIC")
    for role in ("yimatong_app", "yimatong_callback"):
        if op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
            op.execute(f"REVOKE ALL ON FUNCTION public.{_LEGACY_FUNCTION} FROM {role}")


def upgrade() -> None:
    _preflight_member_facts()
    _preflight_index_names()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout='{_LOCK_TIMEOUT}'")
        op.execute(f"SET statement_timeout='{_STATEMENT_TIMEOUT}'")
        try:
            _wait_for_preexisting_snapshots()
            try:
                for name, (unique, expected, create_sql) in _INDEXES.items():
                    _prepare(name, unique, expected, create_sql)
            except BaseException:
                for name in reversed(tuple(_INDEXES)):
                    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
                raise
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")
    _preserve_legacy_function()


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        op.execute("SET statement_timeout='5s'")
        try:
            for name in reversed(tuple(_INDEXES)):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_LEGACY_FUNCTION}")
