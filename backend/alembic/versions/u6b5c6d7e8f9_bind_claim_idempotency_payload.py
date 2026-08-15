"""bind campaign claim idempotency to the authoritative request payload

Revision ID: u6b5c6d7e8f9
Revises: u7c2a3b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u6b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "u7c2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "yimatong_app"
_BOUND_SIGNATURE = "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)"

_BOUND_CLAIM_SQL = r"""
CREATE OR REPLACE FUNCTION public.claim_campaign_benefit(
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
DECLARE benefit_campaign_id uuid; DECLARE digest_value text; DECLARE existing_digest text;
DECLARE claim_result record;
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
    digest_value:=encode(digest(convert_to(jsonb_build_array(
        1,requested_tenant_id,requested_benefit_id,requested_scan_event_id,
        requested_scanned_product_id,requested_public_id,requested_consumer_id,
        requested_idempotency_key,item_probe.code_batch_id,requested_launch_release_id,
        requested_launch_campaign_id,requested_launch_content_digest
    )::text,'UTF8'),'sha256'),'hex');
    SELECT claim.request_digest INTO existing_digest FROM public.benefit_claims AS claim
    WHERE claim.tenant_id=requested_tenant_id AND claim.idempotency_key=requested_idempotency_key
    ORDER BY claim.id::text LIMIT 1;
    IF FOUND AND existing_digest IS DISTINCT FROM digest_value THEN
        RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='campaign claim idempotency conflict';
    END IF;
    SELECT * INTO claim_result FROM public.claim_campaign_benefit(
        requested_tenant_id,requested_claim_id,requested_benefit_id,requested_scan_event_id,
        requested_scanned_product_id,requested_public_id,requested_consumer_id,requested_idempotency_key
    );
    IF claim_result.created THEN
        UPDATE public.benefit_claims AS claim SET request_digest=digest_value
        WHERE claim.tenant_id=requested_tenant_id AND claim.id=claim_result.claim_id
          AND claim.request_digest IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='campaign claim digest binding failed';
        END IF;
    ELSE
        SELECT claim.request_digest INTO existing_digest FROM public.benefit_claims AS claim
        WHERE claim.tenant_id=requested_tenant_id AND claim.idempotency_key=requested_idempotency_key
        ORDER BY claim.id::text LIMIT 1;
        IF existing_digest IS DISTINCT FROM digest_value THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='campaign claim idempotency conflict';
        END IF;
    END IF;
    outcome:=claim_result.outcome;claim_id:=claim_result.claim_id;campaign_id:=claim_result.campaign_id;
    stock_used:=claim_result.stock_used;outbox_id:=claim_result.outbox_id;created:=claim_result.created;
    reserved_amount:=claim_result.reserved_amount;reservation_status:=claim_result.reservation_status;
    RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='launch-bound claim authority is busy';
END
$function$;
"""

_OLD_BOUND_CLAIM_SQL = r"""
CREATE OR REPLACE FUNCTION public.claim_campaign_benefit(
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
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": _ROLE}).scalar())


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("campaign claim downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    """Block deep immutable-fact crossings before this head changes any bytes."""

    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")).scalar_one():
        raise RuntimeError("cannot downgrade benefit claim request binding while bound claims exist")
    if _destination_is_below("u7c0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM public.risk_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6b5 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM public.diversion_observations)+"
            "(SELECT count(*) FROM public.diversion_action_receipts)+"
            "(SELECT count(*) FROM public.diversion_evidence)+"
            "(SELECT count(*) FROM public.diversion_investigation_history)"
        )
    ).scalar_one():
        raise RuntimeError("u6b5 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4") and bind.execute(
        sa.text("SELECT count(*) FROM public.channel_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6b5 downgrade blocked: channel action receipts are immutable facts")


def upgrade() -> None:
    op.add_column("benefit_claims", sa.Column("request_digest", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_benefit_claims_request_digest",
        "benefit_claims",
        "request_digest IS NULL OR length(request_digest) = 64",
    )
    op.create_index(
        "uq_benefit_claims_bound_idempotency",
        "benefit_claims",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("request_digest IS NOT NULL"),
    )
    op.execute(r"""
      CREATE FUNCTION public.guard_benefit_claim_request_digest()
      RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      BEGIN
        IF OLD.request_digest IS NOT NULL AND NEW.request_digest IS DISTINCT FROM OLD.request_digest THEN
          RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='benefit claim request digest is immutable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.guard_benefit_claim_request_digest() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_benefit_claim_request_digest BEFORE UPDATE OF request_digest "
        "ON public.benefit_claims FOR EACH ROW EXECUTE FUNCTION public.guard_benefit_claim_request_digest()"
    )
    op.execute(_BOUND_CLAIM_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BOUND_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_BOUND_SIGNATURE} TO {_ROLE}")


def downgrade() -> None:
    _downgrade_preflight()
    op.execute(_OLD_BOUND_CLAIM_SQL)
    op.execute("DROP TRIGGER IF EXISTS trg_guard_benefit_claim_request_digest ON public.benefit_claims")
    op.execute("DROP FUNCTION IF EXISTS public.guard_benefit_claim_request_digest()")
    op.drop_index("uq_benefit_claims_bound_idempotency", table_name="benefit_claims")
    op.drop_constraint("ck_benefit_claims_request_digest", "benefit_claims", type_="check")
    op.drop_column("benefit_claims", "request_digest")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BOUND_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_BOUND_SIGNATURE} TO {_ROLE}")
