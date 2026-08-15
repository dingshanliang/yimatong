"""bind benefit claims to the exact current live launch

Revision ID: u5b5b6c7d8e9
Revises: u5b4a5b6c7d8
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5b5b6c7d8e9"
down_revision: str | None = "u5b4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_SIGNATURE = "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)"
_BOUND_SIGNATURE = "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)"

_BOUND_CLAIM_SQL = r"""
CREATE FUNCTION public.claim_campaign_benefit(
    requested_tenant_id uuid,requested_claim_id uuid,requested_benefit_id uuid,
    requested_scan_event_id uuid,requested_scanned_product_id uuid,requested_public_id text,
    requested_consumer_id text,requested_idempotency_key text,
    requested_launch_release_id uuid,requested_launch_campaign_id uuid,
    requested_launch_content_digest text
) RETURNS TABLE(
    outcome text,claim_id uuid,campaign_id uuid,stock_used integer,outbox_id uuid,
    created boolean,reserved_amount integer,reservation_status text
) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE item_probe record; DECLARE release_row record; DECLARE readiness record;
DECLARE benefit_campaign_id uuid;
BEGIN
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
       OR requested_launch_release_id IS NULL OR requested_launch_campaign_id IS NULL
       OR requested_launch_content_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='launch-bound claim context invalid';
    END IF;
    SELECT item.code_batch_id,batch.product_id INTO item_probe
    FROM public.code_items AS item
    JOIN public.code_batches AS batch
      ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id
    WHERE item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id;
    IF NOT FOUND OR item_probe.product_id IS DISTINCT FROM requested_scanned_product_id THEN
        RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='launch-bound code identity unavailable';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
        'launch-code-batch:'||requested_tenant_id::text||':'||item_probe.code_batch_id::text,0
    )) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='launch authority is busy';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
        'campaign:'||requested_tenant_id::text||':'||requested_launch_campaign_id::text,0
    )) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy';
    END IF;
    SELECT * INTO release_row FROM public.launch_releases AS release
    WHERE release.tenant_id=requested_tenant_id
      AND release.id=requested_launch_release_id
      AND release.code_batch_id=item_probe.code_batch_id
      AND release.campaign_id=requested_launch_campaign_id
    FOR SHARE NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='launch-bound release unavailable';
    END IF;
    IF release_row.status<>'live' THEN
        outcome:='launch_release_not_current';claim_id:=NULL;campaign_id:=requested_launch_campaign_id;
        stock_used:=NULL;outbox_id:=NULL;created:=false;reserved_amount:=NULL;reservation_status:=NULL;
        RETURN NEXT;RETURN;
    END IF;
    SELECT * INTO readiness FROM public.compute_launch_readiness(
        requested_tenant_id,release_row.page_version_id,release_row.campaign_id,release_row.code_batch_id
    );
    IF NOT readiness.ready
       OR release_row.brand_confirmation_digest IS DISTINCT FROM readiness.content_digest
       OR release_row.content_digest IS DISTINCT FROM readiness.content_digest
       OR requested_launch_content_digest IS DISTINCT FROM readiness.content_digest THEN
        outcome:='launch_release_not_current';claim_id:=NULL;campaign_id:=requested_launch_campaign_id;
        stock_used:=NULL;outbox_id:=NULL;created:=false;reserved_amount:=NULL;reservation_status:=NULL;
        RETURN NEXT;RETURN;
    END IF;
    SELECT benefit.campaign_id INTO benefit_campaign_id FROM public.benefits AS benefit
    WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit unavailable';
    END IF;
    IF benefit_campaign_id IS DISTINCT FROM requested_launch_campaign_id THEN
        outcome:='benefit_not_in_launch_release';claim_id:=NULL;campaign_id:=requested_launch_campaign_id;
        stock_used:=NULL;outbox_id:=NULL;created:=false;reserved_amount:=NULL;reservation_status:=NULL;
        RETURN NEXT;RETURN;
    END IF;
    RETURN QUERY SELECT * FROM public.claim_campaign_benefit(
        requested_tenant_id,requested_claim_id,requested_benefit_id,requested_scan_event_id,
        requested_scanned_product_id,requested_public_id,requested_consumer_id,requested_idempotency_key
    );
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='launch-bound claim authority is busy';
END
$function$;
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
    op.execute(f"DROP FUNCTION IF EXISTS public.{_BOUND_SIGNATURE}")
    op.execute(_BOUND_CLAIM_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BOUND_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"REVOKE ALL ON FUNCTION public.{_LEGACY_SIGNATURE} FROM yimatong_app")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_BOUND_SIGNATURE} TO yimatong_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{_BOUND_SIGNATURE}")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_LEGACY_SIGNATURE} TO yimatong_app")
