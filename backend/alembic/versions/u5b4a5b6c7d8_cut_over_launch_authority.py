"""cut over launch release authority

Revision ID: u5b4a5b6c7d8
Revises: u5b3f4a5b6c7
Create Date: 2026-08-11
"""

from collections.abc import Sequence
import re

import sqlalchemy as sa

from alembic import op

revision: str = "u5b4a5b6c7d8"
down_revision: str | None = "u5b3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PUBLIC_SIGNATURES = (
    "create_launch_release(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)",
    "confirm_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "launch_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "suspend_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "resume_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "invalidate_launch_release(uuid,uuid,uuid,uuid,uuid,text,text,text)",
    "resolve_current_launch_release(uuid,text)",
)
_OBSERVATION_SIGNATURE = "record_launch_release_valid_scan(uuid,uuid,uuid,timestamp with time zone)"

_INTERNAL_SQL = r"""
CREATE OR REPLACE FUNCTION public.compute_launch_readiness(
    requested_tenant_id uuid, requested_page_version_id uuid,
    requested_campaign_id uuid, requested_code_batch_id uuid
) RETURNS TABLE(manifest jsonb, content_digest text, ready boolean,
                page_template_id uuid, readiness_code_item_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE cb record; pb record; page_row record; campaign_row record;
DECLARE product_row record; brand_row record; tenant_row record;
DECLARE benefit_facts jsonb; asset_facts jsonb; takeover_facts jsonb; sample record;
BEGIN
    SELECT production_batch_id INTO cb FROM public.code_batches
    WHERE tenant_id=requested_tenant_id AND id=requested_code_batch_id;
    IF cb.production_batch_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch code batch is unavailable';
    END IF;
    SELECT * INTO pb FROM public.production_batches
    WHERE tenant_id=requested_tenant_id AND id=cb.production_batch_id FOR SHARE NOWAIT;
    SELECT * INTO cb FROM public.code_batches
    WHERE tenant_id=requested_tenant_id AND id=requested_code_batch_id FOR SHARE NOWAIT;
    IF cb.id IS NULL OR pb.id IS NULL OR cb.production_batch_id IS DISTINCT FROM pb.id
       OR cb.product_id IS DISTINCT FROM pb.product_id OR cb.sku_id IS DISTINCT FROM pb.sku_id THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch batch dependency is unavailable';
    END IF;
    SELECT version.id AS version_id,version.page_template_id,version.status AS version_status,
           version.version AS version_number,version.config_json,version.published_at,
           template.product_id,template.status AS template_status
    INTO page_row FROM public.page_versions AS version
    JOIN public.page_templates AS template
      ON template.tenant_id=version.tenant_id AND template.id=version.page_template_id
    WHERE version.tenant_id=requested_tenant_id AND version.id=requested_page_version_id
    FOR SHARE OF template,version NOWAIT;
    IF page_row.version_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch page dependency is unavailable';
    END IF;
    SELECT * INTO campaign_row FROM public.campaigns
    WHERE tenant_id=requested_tenant_id AND id=requested_campaign_id FOR SHARE NOWAIT;
    IF campaign_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch campaign dependency is unavailable';
    END IF;
    PERFORM benefit.id FROM public.benefits AS benefit
    WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
    ORDER BY benefit.id::text FOR SHARE NOWAIT;
    PERFORM connector.id FROM public.connectors AS connector
    WHERE connector.tenant_id=requested_tenant_id AND connector.id IN (
      SELECT benefit.connector_id FROM public.benefits AS benefit
      WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
        AND benefit.connector_id IS NOT NULL
    ) ORDER BY connector.id::text FOR SHARE NOWAIT;
    SELECT product.id,product.brand_id,product.name,product.description,product.image_url,product.origin,
           product.status INTO product_row
    FROM public.products AS product
    WHERE product.tenant_id=requested_tenant_id AND product.id=cb.product_id FOR SHARE NOWAIT;
    SELECT brand.id,brand.name,brand.logo_url,brand.status INTO brand_row
    FROM public.brands AS brand
    WHERE brand.tenant_id=requested_tenant_id AND brand.id=product_row.brand_id FOR SHARE NOWAIT;
    SELECT tenant.brand_profile,tenant.enabled_features INTO tenant_row
    FROM public.tenants AS tenant WHERE tenant.id=requested_tenant_id FOR SHARE NOWAIT;
    PERFORM asset.id FROM public.product_assets AS asset
    WHERE asset.tenant_id=requested_tenant_id AND asset.product_id=cb.product_id
      AND asset.status='active' AND asset.asset_type IN ('test_report','certificate')
      AND (asset.valid_until IS NULL OR asset.valid_until>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date)
    ORDER BY asset.id::text FOR SHARE NOWAIT;
    -- Canonical brand-controlled public and claim-delivery facts.  Deliberately
    -- exclude per-code scan lifecycle, scan_events, risk_alerts, stock_used,
    -- claimed_budget, claim/outbox and delivery state: those runtime facts
    -- remain transaction-authoritative at scan/claim time and must not
    -- invalidate a confirmed release merely because consumers use it.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',asset.id,'type',asset.asset_type,'name',asset.name,'description',asset.description,
      'issuer',asset.issuer,'valid_until',asset.valid_until,'file_url',asset.file_url,
      'image_url',asset.image_url
    ) ORDER BY asset.id::text),'[]'::jsonb) INTO asset_facts
    FROM public.product_assets AS asset
    WHERE asset.tenant_id=requested_tenant_id AND asset.product_id=cb.product_id
      AND asset.status='active' AND asset.asset_type IN ('test_report','certificate')
      AND (asset.valid_until IS NULL OR asset.valid_until>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date);
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',benefit.id,'name',benefit.name,'type',benefit.benefit_type,'status',benefit.status,
      'config',benefit.config_json::jsonb-'claimed_budget','stock_total',benefit.stock_total,
      'per_person_limit',benefit.per_person_limit,'connector_id',benefit.connector_id,
      'connector_type',connector.connector_type,'connector_enabled',connector.enabled,
      'connector_config',connector.config,
      'connector_secret_sha256',CASE WHEN connector.secrets_encrypted IS NULL THEN NULL
        ELSE encode(public.digest(connector.secrets_encrypted,'sha256'),'hex') END
    ) ORDER BY benefit.id::text),'[]'::jsonb) INTO benefit_facts
    FROM public.benefits AS benefit LEFT JOIN public.connectors AS connector
      ON connector.tenant_id=benefit.tenant_id AND connector.id=benefit.connector_id
    WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id;
    SELECT item.id,item.public_id,item.status,item.code_type,item.code_batch_id INTO sample
    FROM public.code_items AS item
    WHERE item.tenant_id=requested_tenant_id AND item.code_batch_id=requested_code_batch_id
      AND item.status IN ('activated','bound')
    ORDER BY item.id::text LIMIT 1 FOR SHARE NOWAIT;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',project.id,'mode',project.mode,'status',project.status,
      'configuration_version',project.configuration_version,
      'active_route_version_id',project.active_route_version_id
    ) ORDER BY project.id::text),'[]'::jsonb) INTO takeover_facts
    FROM public.takeover_projects AS project WHERE project.tenant_id=requested_tenant_id;
    page_template_id:=page_row.page_template_id;
    readiness_code_item_id:=sample.id;
    ready:=page_row.version_status='published' AND page_row.template_status='active'
      AND page_row.product_id=cb.product_id
      AND campaign_row.status='active' AND campaign_row.product_id=cb.product_id
      AND campaign_row.start_at<=CURRENT_TIMESTAMP AND campaign_row.end_at>CURRENT_TIMESTAMP
      AND cb.status='activated' AND pb.status='active'
      AND pb.production_date<=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date
      AND pb.expiry_date>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date
      AND sample.id IS NOT NULL
      AND (takeover_facts='[]'::jsonb OR (
        takeover_facts @> '[{"mode":"legacy_redirect","status":"completed"}]'::jsonb
        AND takeover_facts @> '[{"mode":"cname","status":"completed"}]'::jsonb
        AND NOT jsonb_path_exists(takeover_facts,'$[*] ? (@.status != "completed" && @.status != "rolled_back")')
      ));
    manifest:=jsonb_build_object(
      'version',3,'tenant_id',requested_tenant_id,
      'page',jsonb_build_object('template_id',page_row.page_template_id,'template_status',page_row.template_status,
        'product_id',page_row.product_id,'version_id',page_row.version_id,'version_number',page_row.version_number,
        'version_status',page_row.version_status,'config',page_row.config_json,'published_at',page_row.published_at),
      'campaign',jsonb_build_object('id',campaign_row.id,'name',campaign_row.name,
        'product_id',campaign_row.product_id,
        'status',campaign_row.status,'start_at',campaign_row.start_at,'end_at',campaign_row.end_at,
        'rules',campaign_row.rules_json),
      'benefits',benefit_facts,
      'product',jsonb_build_object('id',product_row.id,'brand_id',product_row.brand_id,
        'name',product_row.name,'description',product_row.description,'image_url',product_row.image_url,
        'origin',product_row.origin,'status',product_row.status),
      'brand',jsonb_build_object('id',brand_row.id,'name',brand_row.name,
        'logo_url',brand_row.logo_url,'status',brand_row.status),
      'tenant_branding',jsonb_build_object('brand_profile',tenant_row.brand_profile,
        'enabled_features',tenant_row.enabled_features),
      'public_assets',asset_facts,
      'code_batch',jsonb_build_object('id',cb.id,'product_id',cb.product_id,'sku_id',cb.sku_id,
        'production_batch_id',cb.production_batch_id,'status',cb.status),
      'production_batch',jsonb_build_object('id',pb.id,'product_id',pb.product_id,'sku_id',pb.sku_id,
        'batch_code',pb.batch_code,'origin',pb.origin,'status',pb.status,
        'production_date',pb.production_date,'expiry_date',pb.expiry_date,
        'recall_reason',pb.recall_reason,'recalled_at',pb.recalled_at),
      'sample_code',jsonb_build_object('id',sample.id,'public_id',sample.public_id,
        'status',sample.status,'code_type',sample.code_type,'code_batch_id',sample.code_batch_id),
      'takeover',takeover_facts,'ready',ready
    );
    content_digest:=encode(public.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex');
    RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch dependency is concurrently changing';
END;
$function$;

CREATE OR REPLACE FUNCTION public.authorize_launch_actor(
  requested_tenant_id uuid,requested_auth_session_id uuid,requested_action text
) RETURNS TABLE(actor_id uuid,principal_tenant_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE required_permission text; matched uuid;
BEGIN
  required_permission:=CASE WHEN requested_action='create' THEN 'page:create' ELSE 'page:publish' END;
  SELECT authorized.actor_id,authorized.principal_tenant_id INTO actor_id,principal_tenant_id
  FROM public.authorize_page_actor(requested_tenant_id,requested_auth_session_id,required_permission) AS authorized;
  IF requested_action IN ('confirm','suspend','resume','invalidate') THEN
    IF principal_tenant_id IS DISTINCT FROM requested_tenant_id OR NOT EXISTS(
      SELECT 1 FROM public.account_roles ar JOIN public.roles role
        ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
      WHERE ar.tenant_id=principal_tenant_id AND ar.account_id=actor_id AND role.name='admin'
    ) THEN RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='launch action requires a brand admin'; END IF;
  ELSIF requested_action='launch' AND principal_tenant_id IS DISTINCT FROM requested_tenant_id THEN
    SELECT auth.id INTO matched FROM public.agency_authorizations auth
    WHERE auth.agency_tenant_id=principal_tenant_id AND auth.client_tenant_id=requested_tenant_id
      AND auth.status='active' AND (auth.expires_at IS NULL OR auth.expires_at>CURRENT_TIMESTAMP)
      AND auth.scope::jsonb ? 'release:execute' FOR SHARE;
    IF matched IS NULL THEN RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='launch actor lacks release execute authority'; END IF;
  ELSIF requested_action='launch' AND NOT EXISTS(
    SELECT 1 FROM public.account_roles ar JOIN public.roles role
      ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
    WHERE ar.tenant_id=principal_tenant_id AND ar.account_id=actor_id AND role.name='admin'
  ) THEN RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='brand launch requires an admin';
  END IF;
  RETURN NEXT;
END;
$function$;

CREATE OR REPLACE FUNCTION public.mutate_launch_release(
  requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
  requested_action_id uuid,requested_release_id uuid,requested_action text,
  requested_page_version_id uuid,requested_campaign_id uuid,requested_code_batch_id uuid,
  requested_expected_digest text,requested_reason text,requested_idempotency_key text
) RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE actor uuid; principal uuid; release_row record; readiness record; receipt record;
DECLARE canonical_payload jsonb; payload_hash text; now_at timestamptz:=CURRENT_TIMESTAMP; cb_id uuid;
DECLARE audit_action text;
BEGIN
  IF requested_action NOT IN ('create','confirm','launch','suspend','resume','invalidate')
     OR requested_release_id IS NULL OR requested_action_id IS NULL OR requested_audit_id IS NULL
     OR NULLIF(btrim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>100 THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='launch mutation parameters are invalid';
  END IF;
  IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='launch tenant context mismatch';
  END IF;
  SELECT authorized.actor_id,authorized.principal_tenant_id INTO actor,principal
  FROM public.authorize_launch_actor(requested_tenant_id,requested_auth_session_id,requested_action) authorized;
  IF NOT pg_try_advisory_xact_lock(hashtextextended(
    'launch-idempotency:'||requested_tenant_id::text||':'||requested_idempotency_key,0
  )) THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch idempotency key is concurrently changing';
  END IF;
  canonical_payload:=jsonb_build_object('action',requested_action,
    'release_id',CASE WHEN requested_action='create' THEN NULL ELSE requested_release_id END,
    'page_version_id',requested_page_version_id,'campaign_id',requested_campaign_id,
    'code_batch_id',requested_code_batch_id,'expected_digest',requested_expected_digest,
    'reason',requested_reason,'actor_id',actor,'actor_tenant_id',principal);
  payload_hash:=encode(public.digest(convert_to(canonical_payload::text,'UTF8'),'sha256'),'hex');
  SELECT * INTO receipt FROM public.launch_release_actions
  WHERE tenant_id=requested_tenant_id AND idempotency_key=requested_idempotency_key FOR SHARE;
  IF receipt.id IS NOT NULL THEN
    IF receipt.action<>requested_action OR receipt.payload_hash<>payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='launch idempotency payload mismatch';
    END IF;
    release_id:=receipt.release_id;status:=receipt.result_status;content_digest:=receipt.result_digest;
    ready:=status IN ('pending_confirmation','confirmed','live','suspended');replayed:=true;
    recorded_at:=receipt.created_at;RETURN NEXT;RETURN;
  END IF;
  IF requested_action='create' THEN cb_id:=requested_code_batch_id;
  ELSE
    SELECT code_batch_id INTO cb_id FROM public.launch_releases
    WHERE tenant_id=requested_tenant_id AND id=requested_release_id;
    IF cb_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release is unavailable'; END IF;
  END IF;
  IF NOT pg_try_advisory_xact_lock(hashtextextended('launch-code-batch:'||requested_tenant_id::text||':'||cb_id::text,0)) THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing';
  END IF;
  IF requested_action='create' THEN
    SELECT * INTO readiness FROM public.compute_launch_readiness(
      requested_tenant_id,requested_page_version_id,requested_campaign_id,requested_code_batch_id);
    INSERT INTO public.launch_releases(
      id,tenant_id,page_template_id,page_version_id,campaign_id,code_batch_id,status,
      readiness_snapshot,readiness_manifest,readiness_code_item_id,content_digest,
      created_by,created_by_tenant_id,created_at,updated_at
    ) VALUES (
      requested_release_id,requested_tenant_id,readiness.page_template_id,requested_page_version_id,
      requested_campaign_id,requested_code_batch_id,
      CASE WHEN readiness.ready THEN 'pending_confirmation' ELSE 'preparing' END,
      jsonb_build_object('version',3,'ready',readiness.ready),readiness.manifest,
      readiness.readiness_code_item_id,readiness.content_digest,actor,principal,now_at,now_at
    ) RETURNING * INTO release_row;
  ELSE
    SELECT * INTO release_row FROM public.launch_releases
    WHERE tenant_id=requested_tenant_id AND id=requested_release_id FOR UPDATE NOWAIT;
    IF release_row.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release is unavailable'; END IF;
    SELECT * INTO readiness FROM public.compute_launch_readiness(
      requested_tenant_id,release_row.page_version_id,release_row.campaign_id,release_row.code_batch_id);
    IF requested_expected_digest IS NOT NULL AND requested_expected_digest<>readiness.content_digest THEN
      RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='launch readiness digest is stale';
    END IF;
    UPDATE public.launch_releases SET readiness_manifest=readiness.manifest,
      readiness_code_item_id=readiness.readiness_code_item_id,content_digest=readiness.content_digest,
      readiness_snapshot=jsonb_build_object('version',3,'ready',readiness.ready),updated_at=now_at
    WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    IF requested_action='confirm' THEN
      IF release_row.status NOT IN ('preparing','pending_confirmation') OR NOT readiness.ready THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='launch release is not ready for confirmation'; END IF;
      UPDATE public.launch_releases SET status='confirmed',brand_confirmed_by=actor,
        brand_confirmed_by_tenant_id=principal,brand_confirmed_at=now_at,
        brand_confirmation_digest=readiness.content_digest,updated_at=now_at
      WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    ELSIF requested_action='launch' THEN
      IF release_row.status<>'confirmed' OR NOT readiness.ready
         OR release_row.brand_confirmation_digest IS DISTINCT FROM readiness.content_digest THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='launch release lacks current brand confirmation'; END IF;
      UPDATE public.launch_releases AS existing SET status='invalidated',invalidated_at=now_at,
        invalidation_reason='superseded',failure_reason='superseded by a newer live release',updated_at=now_at
      WHERE existing.tenant_id=requested_tenant_id AND existing.code_batch_id=release_row.code_batch_id
        AND existing.status='live' AND existing.id<>requested_release_id;
      UPDATE public.launch_releases SET status='live',launched_by=actor,launched_by_tenant_id=principal,
        launched_at=now_at,idempotency_key=requested_idempotency_key,invalidated_at=NULL,
        invalidation_reason=NULL,failure_reason=NULL,updated_at=now_at
      WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    ELSIF requested_action='suspend' THEN
      IF release_row.status<>'live' OR NULLIF(btrim(requested_reason),'') IS NULL OR length(requested_reason)>500 THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='live launch release and reason are required'; END IF;
      UPDATE public.launch_releases SET status='suspended',suspended_by=actor,suspended_by_tenant_id=principal,
        suspended_at=now_at,suspension_reason=btrim(requested_reason),updated_at=now_at
      WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    ELSIF requested_action='resume' THEN
      IF release_row.status<>'suspended' OR NOT readiness.ready
         OR release_row.brand_confirmation_digest IS DISTINCT FROM readiness.content_digest THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='suspended release requires new brand confirmation';
      END IF;
      UPDATE public.launch_releases SET status='live',suspended_by=NULL,suspended_by_tenant_id=NULL,
        suspended_at=NULL,suspension_reason=NULL,updated_at=now_at
      WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    ELSE
      IF release_row.status NOT IN ('confirmed','live','suspended') OR requested_reason NOT IN
        ('page_changed','campaign_changed','benefit_changed','connector_changed','code_batch_changed','production_batch_changed') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='launch invalidation is not applicable'; END IF;
      UPDATE public.launch_releases SET status='invalidated',invalidated_at=now_at,
        invalidation_reason=requested_reason,failure_reason='launch dependency changed',updated_at=now_at
      WHERE tenant_id=requested_tenant_id AND id=requested_release_id RETURNING * INTO release_row;
    END IF;
  END IF;
  audit_action:=CASE requested_action
    WHEN 'create' THEN 'launch_release_created'
    WHEN 'confirm' THEN 'launch_release_confirmed'
    WHEN 'launch' THEN 'launch_release_launched'
    WHEN 'suspend' THEN 'launch_release_suspended'
    WHEN 'resume' THEN 'launch_release_resumed'
    ELSE 'launch_release_invalidated' END;
  INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
  VALUES(requested_audit_id,actor::text,requested_tenant_id::text,audit_action,
    'launch_release:'||requested_release_id::text,
    jsonb_build_object('action_id',requested_action_id,'payload_hash',payload_hash,
      'content_digest',release_row.content_digest,'status',release_row.status),now_at,now_at,now_at);
  INSERT INTO public.launch_release_actions(id,tenant_id,release_id,action,idempotency_key,payload_hash,
    actor_tenant_id,actor_id,result_status,result_digest,replayed,created_at)
  VALUES(requested_action_id,requested_tenant_id,requested_release_id,requested_action,
    requested_idempotency_key,payload_hash,principal,actor,release_row.status,release_row.content_digest,false,now_at);
  release_id:=release_row.id;status:=release_row.status;content_digest:=release_row.content_digest;
  ready:=readiness.ready;replayed:=false;recorded_at:=now_at;RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing';
END;
$function$;

CREATE OR REPLACE FUNCTION public.record_launch_release_valid_scan(
  requested_tenant_id uuid,requested_release_id uuid,requested_scan_event_id uuid,
  requested_scan_time timestamptz
) RETURNS TABLE(release_id uuid,observation_status text,recorded_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE release_row record; matched_event record; now_at timestamptz:=CURRENT_TIMESTAMP;
BEGIN
  IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='launch scan observation tenant context mismatch'; END IF;
  SELECT release.code_batch_id INTO release_row FROM public.launch_releases AS release
  WHERE release.tenant_id=requested_tenant_id AND release.id=requested_release_id;
  IF release_row.code_batch_id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release is unavailable'; END IF;
  IF NOT pg_try_advisory_xact_lock(hashtextextended(
      'launch-code-batch:'||requested_tenant_id::text||':'||release_row.code_batch_id::text,0)) THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing'; END IF;
  SELECT * INTO release_row FROM public.launch_releases AS release
  WHERE release.tenant_id=requested_tenant_id AND release.id=requested_release_id FOR UPDATE NOWAIT;
  IF release_row.status<>'live' OR release_row.launched_at IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='launch release is not live'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.scan_events AS event WHERE event.tenant_id=requested_tenant_id
      AND event.id=requested_scan_event_id AND event.scan_time=requested_scan_time
  ) THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan event is unavailable'; END IF;
  SELECT event.id,event.scan_time INTO matched_event FROM public.scan_events AS event
  JOIN public.code_items AS item ON item.tenant_id=event.tenant_id AND item.public_id=event.public_id
  WHERE event.tenant_id=requested_tenant_id AND event.id=requested_scan_event_id
    AND event.scan_time=requested_scan_time AND event.is_valid_visit IS TRUE
    AND event.scan_time>=release_row.launched_at AND item.code_batch_id=release_row.code_batch_id;
  IF matched_event.id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='scan event is not valid launch evidence'; END IF;
  IF release_row.first_valid_scan_event_id IS NOT NULL THEN
    release_id:=release_row.id;
    observation_status:=CASE
      WHEN release_row.first_valid_scan_event_id=requested_scan_event_id
       AND release_row.first_valid_scan_time=requested_scan_time THEN 'observed'
      ELSE 'already_observed' END;
    recorded_at:=release_row.first_valid_scan_time;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  UPDATE public.launch_releases AS release SET first_valid_scan_event_id=matched_event.id,
    first_valid_scan_time=matched_event.scan_time,updated_at=now_at
  WHERE release.tenant_id=requested_tenant_id AND release.id=requested_release_id RETURNING * INTO release_row;
  INSERT INTO public.platform_audit_log(
    id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
  ) VALUES (
    requested_scan_event_id,'system:public-resolver',requested_tenant_id::text,
    'launch_release_first_valid_scan_observed','launch_release:'||requested_release_id::text,
    jsonb_build_object('scan_event_id',requested_scan_event_id,'scan_time',requested_scan_time,
      'code_batch_id',release_row.code_batch_id),now_at,now_at,now_at
  );
  release_id:=release_row.id;observation_status:='observed';recorded_at:=matched_event.scan_time;
  replayed:=false;RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing';
END;
$function$;
"""

_WRAPPERS_SQL = r"""
CREATE OR REPLACE FUNCTION public.create_launch_release(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'create',$6,$7,$8,NULL,NULL,$9) $$;
CREATE OR REPLACE FUNCTION public.confirm_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'confirm',NULL,NULL,NULL,$6,NULL,$7) $$;
CREATE OR REPLACE FUNCTION public.launch_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'launch',NULL,NULL,NULL,$6,NULL,$7) $$;
CREATE OR REPLACE FUNCTION public.suspend_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'suspend',NULL,NULL,NULL,NULL,$6,$7) $$;
CREATE OR REPLACE FUNCTION public.resume_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'resume',NULL,NULL,NULL,$6,NULL,$7) $$;
CREATE OR REPLACE FUNCTION public.invalidate_launch_release(uuid,uuid,uuid,uuid,uuid,text,text,text)
RETURNS TABLE(release_id uuid,status text,content_digest text,ready boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT * FROM public.mutate_launch_release($1,$2,$3,$4,$5,'invalidate',NULL,NULL,NULL,$6,$7,$8) $$;

CREATE OR REPLACE FUNCTION public.resolve_current_launch_release(requested_tenant_id uuid,requested_public_id text)
RETURNS TABLE(release_id uuid,page_template_id uuid,page_version_id uuid,campaign_id uuid,
              code_batch_id uuid,content_digest text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE item_batch uuid; current_release record; readiness record; now_at timestamptz:=CURRENT_TIMESTAMP;
BEGIN
  IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='launch resolver tenant context mismatch'; END IF;
  SELECT item.code_batch_id INTO item_batch FROM public.code_items item
  WHERE item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id;
  IF item_batch IS NULL THEN RETURN; END IF;
  IF NOT pg_try_advisory_xact_lock(hashtextextended('launch-code-batch:'||requested_tenant_id::text||':'||item_batch::text,0)) THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing'; END IF;
  SELECT * INTO current_release FROM public.launch_releases release
  WHERE release.tenant_id=requested_tenant_id AND release.code_batch_id=item_batch AND release.status='live'
  FOR UPDATE NOWAIT;
  IF current_release.id IS NULL THEN RETURN; END IF;
  SELECT * INTO readiness FROM public.compute_launch_readiness(requested_tenant_id,
    current_release.page_version_id,current_release.campaign_id,current_release.code_batch_id);
  IF NOT readiness.ready OR current_release.brand_confirmation_digest IS DISTINCT FROM readiness.content_digest
     OR current_release.content_digest IS DISTINCT FROM readiness.content_digest THEN
    UPDATE public.launch_releases SET status='invalidated',readiness_manifest=readiness.manifest,
      readiness_code_item_id=readiness.readiness_code_item_id,content_digest=readiness.content_digest,
      invalidated_at=now_at,invalidation_reason='resolver_dependency_changed',
      failure_reason='resolver detected launch dependency drift',updated_at=now_at
    WHERE tenant_id=requested_tenant_id AND id=current_release.id;
    RETURN;
  END IF;
  release_id:=current_release.id;page_template_id:=current_release.page_template_id;
  page_version_id:=current_release.page_version_id;campaign_id:=current_release.campaign_id;
  code_batch_id:=current_release.code_batch_id;content_digest:=current_release.content_digest;RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch release is concurrently changing';
END;
$function$;
"""


def _runtime_role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar_one())


def _execute_sql_batch(sql: str) -> None:
    """Execute a DDL batch without splitting dollar-quoted function bodies."""
    statements: list[str] = []
    start = index = 0
    quote: str | None = None
    while index < len(sql):
        if quote is not None:
            if sql.startswith(quote, index):
                index += len(quote)
                quote = None
            else:
                index += 1
            continue
        if sql[index] in ("'", '"'):
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                quote = match.group(0)
                index += len(quote)
                continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    if trailing := sql[start:].strip():
        statements.append(trailing)
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    bind = op.get_bind()
    # Old digests only hashed readiness booleans. They cannot be promoted into
    # the canonical v2 manifest without a fresh brand confirmation.
    bind.execute(sa.text("""
      UPDATE public.launch_releases SET status='invalidated',invalidated_at=CURRENT_TIMESTAMP,
        invalidation_reason='authority_cutover',failure_reason='reconfirmation required after launch authority cutover'
      WHERE status IN ('confirmed','live','suspended')
        AND readiness_manifest->>'version' IS DISTINCT FROM '3'
    """))
    _execute_sql_batch(_INTERNAL_SQL)
    _execute_sql_batch(_WRAPPERS_SQL)
    for signature in ("compute_launch_readiness(uuid,uuid,uuid,uuid)", "authorize_launch_actor(uuid,uuid,text)",
                      "mutate_launch_release(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,uuid,text,text,text)"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _runtime_role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
    for signature in _PUBLIC_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _runtime_role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_OBSERVATION_SIGNATURE} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_OBSERVATION_SIGNATURE} TO yimatong_app")
    if _runtime_role_exists():
        op.execute("REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON public.launch_releases FROM yimatong_app")
        op.execute("GRANT SELECT ON public.launch_releases TO yimatong_app")
        op.execute("REVOKE ALL ON public.launch_release_actions FROM yimatong_app")
        op.execute("GRANT SELECT ON public.launch_release_actions TO yimatong_app")
    for constraint in (
        "launch_releases_campaign_id_fkey","launch_releases_code_batch_id_fkey",
        "launch_releases_created_by_fkey","launch_releases_brand_confirmed_by_fkey",
        "launch_releases_launched_by_fkey","launch_releases_suspended_by_fkey",
    ):
        op.execute(f"ALTER TABLE public.launch_releases DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute("DROP TRIGGER IF EXISTS trg_bind_launch_release_actor_tenants ON public.launch_releases")
    op.execute("DROP FUNCTION IF EXISTS public.bind_launch_release_actor_tenants()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.launch_release_actions")).scalar_one():
        raise RuntimeError("downgrade requires archiving launch release action receipts")
    for signature in reversed(_PUBLIC_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_OBSERVATION_SIGNATURE}")
    op.execute("DROP FUNCTION IF EXISTS public.mutate_launch_release(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,uuid,text,text,text)")
    op.execute("DROP FUNCTION IF EXISTS public.authorize_launch_actor(uuid,uuid,text)")
    op.execute("DROP FUNCTION IF EXISTS public.compute_launch_readiness(uuid,uuid,uuid,uuid)")
    for name, definition in _LEGACY_FKS:
        op.execute(f"ALTER TABLE public.launch_releases ADD CONSTRAINT {name} {definition} NOT VALID")
        op.execute(f"ALTER TABLE public.launch_releases VALIDATE CONSTRAINT {name}")
    _execute_sql_batch(_COMPAT_DOWNGRADE_SQL)
    if _runtime_role_exists():
        op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.launch_releases TO yimatong_app")


_COMPAT_DOWNGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION public.bind_launch_release_actor_tenants()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
BEGIN
  SELECT tenant_id INTO NEW.created_by_tenant_id FROM public.accounts WHERE id=NEW.created_by;
  IF NEW.created_by_tenant_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='launch release creator is unavailable'; END IF;
  IF NEW.brand_confirmed_by IS NOT NULL THEN SELECT tenant_id INTO NEW.brand_confirmed_by_tenant_id FROM public.accounts WHERE id=NEW.brand_confirmed_by; END IF;
  IF NEW.launched_by IS NOT NULL THEN SELECT tenant_id INTO NEW.launched_by_tenant_id FROM public.accounts WHERE id=NEW.launched_by; END IF;
  IF NEW.suspended_by IS NOT NULL THEN SELECT tenant_id INTO NEW.suspended_by_tenant_id FROM public.accounts WHERE id=NEW.suspended_by; END IF;
  RETURN NEW;
END;$function$;
REVOKE ALL ON FUNCTION public.bind_launch_release_actor_tenants() FROM PUBLIC;
DROP TRIGGER IF EXISTS trg_bind_launch_release_actor_tenants ON public.launch_releases;
CREATE TRIGGER trg_bind_launch_release_actor_tenants BEFORE INSERT OR UPDATE OF created_by,brand_confirmed_by,launched_by,suspended_by
ON public.launch_releases FOR EACH ROW EXECUTE FUNCTION public.bind_launch_release_actor_tenants();
"""

_LEGACY_FKS = (
    ("launch_releases_campaign_id_fkey", "FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id)"),
    ("launch_releases_code_batch_id_fkey", "FOREIGN KEY (code_batch_id) REFERENCES public.code_batches(id)"),
    ("launch_releases_created_by_fkey", "FOREIGN KEY (created_by) REFERENCES public.accounts(id)"),
    ("launch_releases_brand_confirmed_by_fkey", "FOREIGN KEY (brand_confirmed_by) REFERENCES public.accounts(id)"),
    ("launch_releases_launched_by_fkey", "FOREIGN KEY (launched_by) REFERENCES public.accounts(id)"),
    ("launch_releases_suspended_by_fkey", "FOREIGN KEY (suspended_by) REFERENCES public.accounts(id)"),
)
