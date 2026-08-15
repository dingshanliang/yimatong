"""add subject-bound consumer consent receipt status

Revision ID: u6e4a5b6c7d8
Revises: u6e3f4a5b6c7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6e4a5b6c7d8"
down_revision: str | None = "u6e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNATURE = "get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid)"

_FUNCTION_SQL = r"""
CREATE FUNCTION public.get_consumer_consent_receipt_status(
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
       AND ((receipt.consumer_id IS NULL AND requested_token_consumer_id IS NULL)
         OR receipt.consumer_id=requested_token_consumer_id)
     FOR SHARE OF receipt NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent receipt is busy';
END $f$;
"""


def _role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"))
        .scalar_one()
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SIGNATURE}")
    op.execute(_FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_SIGNATURE} TO yimatong_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SIGNATURE}")
