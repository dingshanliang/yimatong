"""Bind page-version mutations to launch invalidation in the same transaction.

Revision ID: u6g0d1e2f3a4
Revises: u6f2c3d4e5f6
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6g0d1e2f3a4"
down_revision: str | None = "u6f2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_INTERNAL_SIGNATURE = (
    "mutate_page_version_authority(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb)"
)
_BASE_SIGNATURE = (
    "mutate_page_version_authority_without_launch_invalidation"
    "(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb)"
)
_HELPER_SIGNATURE = (
    "invalidate_launch_releases_for_page_mutation"
    "(uuid,uuid,uuid,text,uuid,timestamp with time zone)"
)

_HELPER_SQL = r"""
CREATE FUNCTION public.invalidate_launch_releases_for_page_mutation(
    requested_tenant_id uuid,
    requested_template_id uuid,
    requested_version_id uuid,
    requested_page_action text,
    requested_page_audit_id uuid,
    requested_now timestamptz
) RETURNS uuid[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE candidate record;
DECLARE invalidated_ids uuid[] := ARRAY[]::uuid[];
DECLARE locked_ids uuid[] := ARRAY[]::uuid[];
BEGIN
    IF requested_tenant_id IS NULL OR requested_template_id IS NULL
       OR requested_version_id IS NULL OR requested_page_audit_id IS NULL
       OR requested_now IS NULL
       OR requested_page_action NOT IN ('create','update','publish','archive','rollback') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page launch invalidation parameters are invalid';
    END IF;
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page launch invalidation tenant context mismatch';
    END IF;

    FOR candidate IN
        SELECT release.id
        FROM public.launch_releases AS release
        WHERE release.tenant_id=requested_tenant_id
          AND release.page_template_id=requested_template_id
          AND (
              requested_page_action IN ('create','rollback')
              OR release.page_version_id=requested_version_id
          )
        ORDER BY release.code_batch_id::text,release.id::text
        FOR UPDATE NOWAIT
    LOOP
        locked_ids:=array_append(locked_ids,candidate.id);
    END LOOP;

    IF cardinality(locked_ids)>0 THEN
        WITH changed AS (
            UPDATE public.launch_releases AS release
            SET status='invalidated',invalidated_at=requested_now,
                invalidation_reason='page_changed',
                failure_reason='launch page dependency changed',updated_at=requested_now
            WHERE release.id=ANY(locked_ids)
              AND release.tenant_id=requested_tenant_id
              AND release.status IN ('confirmed','live','suspended')
            RETURNING release.id
        )
        SELECT COALESCE(array_agg(changed.id ORDER BY changed.id::text),ARRAY[]::uuid[])
        INTO invalidated_ids FROM changed;
    END IF;

    UPDATE public.platform_audit_log AS audit
    SET details=(COALESCE(audit.details,'{}'::json)::jsonb || jsonb_build_object(
        'launch_invalidation',jsonb_build_object(
            'page_action',requested_page_action,
            'release_ids',to_jsonb(invalidated_ids),
            'release_count',cardinality(invalidated_ids)
        )
    ))::json
    WHERE audit.id=requested_page_audit_id
      AND audit.target_tenant_id=requested_tenant_id::text
      AND audit.resource='page_version:' || requested_version_id::text;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page mutation audit binding is unavailable';
    END IF;
    RETURN invalidated_ids;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing';
END;
$function$
"""

_WRAPPER_SQL = r"""
CREATE FUNCTION public.mutate_page_version_authority(
    requested_tenant_id uuid,
    requested_auth_session_id uuid,
    requested_audit_id uuid,
    requested_action text,
    requested_version_id uuid,
    requested_template_id uuid,
    requested_source_version_id uuid,
    requested_config jsonb
) RETURNS TABLE (
    page_version_id uuid,
    page_template_id uuid,
    source_version_id uuid,
    version_number integer,
    current_status text,
    published_at timestamptz,
    updated_at timestamptz,
    recorded_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE result_row record;
BEGIN
    SELECT * INTO result_row
    FROM public.mutate_page_version_authority_without_launch_invalidation(
        requested_tenant_id,requested_auth_session_id,requested_audit_id,requested_action,
        requested_version_id,requested_template_id,requested_source_version_id,requested_config
    );
    IF result_row.page_version_id IS NULL THEN
        RETURN;
    END IF;
    PERFORM public.invalidate_launch_releases_for_page_mutation(
        requested_tenant_id,result_row.page_template_id,result_row.page_version_id,
        requested_action,requested_audit_id,result_row.recorded_at
    );
    page_version_id:=result_row.page_version_id;
    page_template_id:=result_row.page_template_id;
    source_version_id:=result_row.source_version_id;
    version_number:=result_row.version_number;
    current_status:=result_row.current_status;
    published_at:=result_row.published_at;
    updated_at:=result_row.updated_at;
    recorded_at:=result_row.recorded_at;
    RETURN NEXT;
END;
$function$
"""


def _role_exists() -> bool:
    return bool(op.get_bind().exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname='yimatong_app'").scalar())


def _assert_internal_catalog() -> None:
    for signature in (_INTERNAL_SIGNATURE, _BASE_SIGNATURE, _HELPER_SIGNATURE):
        row = op.get_bind().execute(
            sa.text(
                "SELECT procedure.prosecdef,procedure.proconfig "
                "FROM pg_proc AS procedure WHERE procedure.oid=to_regprocedure(:signature)"
            ),
            {"signature": f"public.{signature}"},
        ).one_or_none()
        if row is None or row[0] is not True or row[1] != ["search_path=pg_catalog, public"]:
            raise RuntimeError(f"page launch authority function public.{signature} is not exact and secure")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().execute(
        sa.text("SELECT to_regprocedure(:signature) IS NOT NULL"),
        {"signature": f"public.{_BASE_SIGNATURE}"},
    ).scalar():
        raise RuntimeError("page authority launch-invalidation base already exists")
    op.execute(
        "ALTER FUNCTION public.mutate_page_version_authority"
        "(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb) "
        "RENAME TO mutate_page_version_authority_without_launch_invalidation"
    )
    op.execute(_HELPER_SQL)
    op.execute(_WRAPPER_SQL)
    _assert_internal_catalog()
    for signature in (_INTERNAL_SIGNATURE, _BASE_SIGNATURE, _HELPER_SIGNATURE):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {_RUNTIME_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION public.{_INTERNAL_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_HELPER_SIGNATURE}")
    op.execute(
        "ALTER FUNCTION public.mutate_page_version_authority_without_launch_invalidation"
        "(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb) RENAME TO mutate_page_version_authority"
    )
