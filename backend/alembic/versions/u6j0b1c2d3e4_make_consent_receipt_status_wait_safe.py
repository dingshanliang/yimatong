"""Make consumer consent receipt status wait-safe.

Revision ID: u6j0b1c2d3e4
Revises: u6i0a1b2c3d4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u6j0b1c2d3e4"
down_revision: str | None = "u6i0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_STATUS = "get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid)"

_WAIT_SAFE_STATUS_SQL = r"""
CREATE OR REPLACE FUNCTION public.get_consumer_consent_receipt_status(
    requested_tenant_id uuid,requested_consent_id uuid,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid
) RETURNS TABLE(
    consent_id uuid,status text,purpose text,policy_version text,policy_digest text,
    consumer_id uuid,granted_at timestamptz,withdrawn_at timestamptz
) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE subject_hash text;
BEGIN
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
       OR requested_visitor_id IS NULL OR btrim(requested_visitor_id)=''
       OR requested_public_id IS NULL OR btrim(requested_public_id)='' THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;

    -- The resolver updates the visitor before recording a scan. A read-only
    -- receipt lookup waits on that same first resource, without taking the
    -- mutation validator's NOWAIT visitor write lock. This ordering prevents
    -- a normal reload from being reported as a transient authority denial.
    PERFORM 1 FROM public.anonymous_visitors AS visitor
     WHERE visitor.tenant_id=requested_tenant_id AND visitor.visitor_id=requested_visitor_id
       AND ((requested_token_consumer_id IS NULL AND visitor.consumer_id IS NULL)
         OR visitor.consumer_id=requested_token_consumer_id)
     FOR SHARE OF visitor;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;

    PERFORM 1 FROM public.tenants AS tenant WHERE tenant.id=requested_tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='tenant unavailable';
    END IF;

    PERFORM 1 FROM public.scan_events AS event
      JOIN public.code_items AS item ON item.tenant_id=event.tenant_id AND item.public_id=event.public_id
     WHERE event.tenant_id=requested_tenant_id AND event.id=requested_scan_event_id
       AND event.scan_time=requested_scan_time AND event.public_id=requested_public_id
       AND event.visitor_id=requested_visitor_id AND event.is_valid_visit IS TRUE
       AND item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id
     FOR SHARE OF event,item;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;

    subject_hash:=encode(digest(convert_to(
        requested_tenant_id::text||':'||requested_visitor_id,'UTF8'),'sha256'),'hex');
    RETURN QUERY SELECT receipt.id,receipt.status::text,receipt.purpose::text,
        receipt.policy_version::text,receipt.policy_digest::text,receipt.consumer_id,
        receipt.granted_at,receipt.withdrawn_at
      FROM public.consent_records AS receipt
     WHERE receipt.tenant_id=requested_tenant_id AND receipt.id=requested_consent_id
       AND receipt.authority_version=1 AND receipt.visitor_subject_hash=subject_hash
       AND receipt.public_id=requested_public_id
       AND (receipt.consumer_id IS NULL OR receipt.consumer_id=requested_token_consumer_id)
     FOR SHARE OF receipt;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;
END $f$
"""

_NOWAIT_STATUS_SQL = r"""
CREATE OR REPLACE FUNCTION public.get_consumer_consent_receipt_status(
    requested_tenant_id uuid,requested_consent_id uuid,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid
) RETURNS TABLE(
    consent_id uuid,status text,purpose text,policy_version text,policy_digest text,
    consumer_id uuid,granted_at timestamptz,withdrawn_at timestamptz
) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE subject_hash text;
BEGIN
    subject_hash:=public.validate_consumer_consent_subject(
        requested_tenant_id,requested_scan_event_id,requested_scan_time,
        requested_public_id,requested_visitor_id,requested_token_consumer_id
    );
    RETURN QUERY SELECT receipt.id,receipt.status::text,receipt.purpose::text,
        receipt.policy_version::text,receipt.policy_digest::text,receipt.consumer_id,
        receipt.granted_at,receipt.withdrawn_at
      FROM public.consent_records AS receipt
     WHERE receipt.tenant_id=requested_tenant_id AND receipt.id=requested_consent_id
       AND receipt.authority_version=1 AND receipt.visitor_subject_hash=subject_hash
       AND receipt.public_id=requested_public_id
       AND (receipt.consumer_id IS NULL OR receipt.consumer_id=requested_token_consumer_id)
     FOR SHARE OF receipt NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent receipt is busy';
END $f$
"""


def _apply_status(sql: str) -> None:
    op.execute(sql)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_STATUS} FROM PUBLIC")
    role_exists = op.get_bind().exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"
    ).scalar_one()
    if role_exists:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_STATUS} TO {_RUNTIME_ROLE}")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _apply_status(_WAIT_SAFE_STATUS_SQL)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _apply_status(_NOWAIT_STATUS_SQL)
