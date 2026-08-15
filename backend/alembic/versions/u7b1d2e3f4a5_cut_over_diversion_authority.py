"""cut over diversion observations and investigation mutations

Revision ID: u7b1d2e3f4a5
Revises: u7b0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "u7b1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u7b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "yimatong_app"
_FUNCTIONS = (
    "record_diversion_observation(uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,text,text,boolean,uuid,uuid,text,text)",
    "add_diversion_evidence(uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,text,text)",
    "transition_diversion_clue(uuid,uuid,uuid,uuid,bigint,text,text,text,text)",
)


def _role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": _ROLE}).scalar())


def _install_observation() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.record_diversion_observation(
          requested_tenant_id uuid,requested_observation_id uuid,requested_scan_event_id uuid,
          requested_scan_time timestamptz,requested_idempotency_key text,requested_public_id text,
          requested_code_item_id uuid,requested_ip_hash text,requested_detected_city text,
          requested_expected_region text,requested_location_source text,requested_location_accuracy text,
          requested_location_authorized boolean,requested_distributor_id uuid,requested_region_id uuid,
          requested_rule_name text,requested_confidence text
        ) RETURNS TABLE(resource_id uuid,clue_id uuid,clue_version bigint,investigation_status text,
          resolved boolean,observation_count bigint,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE existing public.diversion_observations%ROWTYPE;
        DECLARE clue public.diversion_clues%ROWTYPE;
        DECLARE scan public.scan_events%ROWTYPE;
        DECLARE digest_value text;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='diversion observation tenant context mismatch';
          END IF;
          IF requested_observation_id IS NULL OR requested_scan_event_id IS NULL OR requested_scan_time IS NULL
             OR requested_code_item_id IS NULL OR NULLIF(trim(requested_idempotency_key),'') IS NULL
             OR length(requested_idempotency_key)>128 OR NULLIF(trim(requested_detected_city),'') IS NULL
             OR length(trim(requested_detected_city))>200 OR NULLIF(trim(requested_expected_region),'') IS NULL
             OR length(trim(requested_expected_region))>200 OR requested_rule_name NOT IN ('cross_region_ip','cross_region_browser')
             OR requested_confidence NOT IN ('high','medium','low')
             OR requested_location_source NOT IN ('ip_inference','browser_geolocation','manual')
             OR requested_location_accuracy NOT IN ('high','medium','low','unknown')
             OR (requested_ip_hash IS NOT NULL AND length(requested_ip_hash)>64) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='diversion observation input is invalid';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('diversion-idem:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_scan_event_id,requested_scan_time,
            requested_public_id,requested_code_item_id,requested_ip_hash,trim(requested_detected_city),
            trim(requested_expected_region),requested_location_source,requested_location_accuracy,
            requested_location_authorized,requested_distributor_id,requested_region_id,requested_rule_name,
            requested_confidence)::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.diversion_observations observation
          WHERE observation.tenant_id=requested_tenant_id AND observation.idempotency_key=requested_idempotency_key;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='diversion observation idempotency conflict'; END IF;
            resource_id:=existing.id;clue_id:=existing.clue_id;clue_version:=existing.clue_version;
            investigation_status:=existing.investigation_status;resolved:=existing.resolved;
            observation_count:=existing.observation_count;replayed:=true;actor_id:=NULL;
            recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO scan FROM public.scan_events event
          WHERE event.tenant_id=requested_tenant_id AND event.id=requested_scan_event_id
            AND event.scan_time=requested_scan_time AND event.public_id=requested_public_id
            AND event.ip_hash IS NOT DISTINCT FROM requested_ip_hash AND event.is_valid_visit FOR SHARE;
          IF scan.id IS NULL OR NOT EXISTS(
            SELECT 1 FROM public.code_items item WHERE item.tenant_id=requested_tenant_id
              AND item.id=requested_code_item_id AND item.public_id=requested_public_id
          ) THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='diversion scan subject is unavailable'; END IF;
          IF requested_distributor_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.distributors d WHERE d.tenant_id=requested_tenant_id AND d.id=requested_distributor_id
          ) THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='diversion distributor is unavailable'; END IF;
          IF requested_region_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.regions r WHERE r.tenant_id=requested_tenant_id AND r.id=requested_region_id
          ) THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='diversion region is unavailable'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('diversion-subject:'||requested_tenant_id::text||':'||requested_public_id||':'||requested_rule_name,0));
          SELECT * INTO existing FROM public.diversion_observations observation
          WHERE observation.tenant_id=requested_tenant_id AND observation.scan_event_id=requested_scan_event_id
            AND observation.rule_name=requested_rule_name;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='diversion scan classification conflicts'; END IF;
            resource_id:=existing.id;clue_id:=existing.clue_id;clue_version:=existing.clue_version;
            investigation_status:=existing.investigation_status;resolved:=existing.resolved;
            observation_count:=existing.observation_count;replayed:=true;actor_id:=NULL;
            recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO clue FROM public.diversion_clues c WHERE c.tenant_id=requested_tenant_id
            AND c.public_id=requested_public_id AND c.rule_name=requested_rule_name FOR UPDATE;
          IF clue.id IS NULL THEN
            clue.id:=gen_random_uuid();clue.tenant_id:=requested_tenant_id;clue.public_id:=requested_public_id;
            clue.code_item_id:=requested_code_item_id;clue.expected_region:=trim(requested_expected_region);
            clue.detected_city:=trim(requested_detected_city);clue.distributor_id:=requested_distributor_id;
            clue.region_id:=requested_region_id;clue.ip_hash:=requested_ip_hash;
            clue.location_source:=requested_location_source;clue.location_accuracy:=requested_location_accuracy;
            clue.location_authorized:=requested_location_authorized;clue.rule_name:=requested_rule_name;
            clue.confidence:=requested_confidence;clue.pending_review:=requested_confidence<>'high';
            clue.observation_count:=1;clue.investigation_status:='open';clue.resolved:=false;clue.version:=1;
            INSERT INTO public.diversion_clues(id,tenant_id,public_id,code_item_id,expected_region,detected_city,
              distributor_id,region_id,ip_hash,location_source,location_accuracy,location_authorized,rule_name,
              confidence,pending_review,observation_count,investigation_status,resolved,version,created_at,updated_at)
            VALUES(clue.id,clue.tenant_id,clue.public_id,clue.code_item_id,clue.expected_region,clue.detected_city,
              clue.distributor_id,clue.region_id,clue.ip_hash,clue.location_source,clue.location_accuracy,
              clue.location_authorized,clue.rule_name,clue.confidence,clue.pending_review,1,'open',false,1,now_at,now_at);
          ELSE
            IF clue.resolved THEN
              INSERT INTO public.diversion_investigation_history(id,tenant_id,clue_id,from_status,to_status,
                changed_by_account_id,changed_at,reason,created_at)
              VALUES(gen_random_uuid(),requested_tenant_id,clue.id,clue.investigation_status,'pending_evidence',NULL,
                now_at,'new qualifying scan observation',now_at);
              clue.investigation_status:='pending_evidence';clue.resolved:=false;
            END IF;
            clue.observation_count:=clue.observation_count+1;clue.version:=clue.version+1;
            UPDATE public.diversion_clues c SET code_item_id=requested_code_item_id,
              expected_region=trim(requested_expected_region),detected_city=trim(requested_detected_city),
              distributor_id=requested_distributor_id,region_id=requested_region_id,ip_hash=requested_ip_hash,
              location_source=requested_location_source,location_accuracy=requested_location_accuracy,
              location_authorized=requested_location_authorized,confidence=requested_confidence,
              pending_review=requested_confidence<>'high',observation_count=clue.observation_count,
              investigation_status=clue.investigation_status,resolved=false,resolution_action=NULL,
              resolution_note=NULL,resolved_by_account_id=NULL,resolved_at=NULL,version=clue.version,updated_at=now_at
            WHERE c.tenant_id=requested_tenant_id AND c.id=clue.id;
          END IF;
          INSERT INTO public.diversion_observations(id,tenant_id,scan_event_id,clue_id,public_id,code_item_id,
            observed_at,ip_hash,detected_city,expected_region,location_source,location_accuracy,location_authorized,
            distributor_id,region_id,rule_name,confidence,clue_version,investigation_status,resolved,
            observation_count,payload_digest,idempotency_key,recorded_at)
          VALUES(requested_observation_id,requested_tenant_id,requested_scan_event_id,clue.id,requested_public_id,
            requested_code_item_id,requested_scan_time,requested_ip_hash,trim(requested_detected_city),
            trim(requested_expected_region),requested_location_source,requested_location_accuracy,
            requested_location_authorized,requested_distributor_id,requested_region_id,requested_rule_name,
            requested_confidence,clue.version,clue.investigation_status,false,clue.observation_count,digest_value,
            requested_idempotency_key,now_at);
          resource_id:=requested_observation_id;clue_id:=clue.id;clue_version:=clue.version;
          investigation_status:=clue.investigation_status;resolved:=false;observation_count:=clue.observation_count;
          replayed:=false;actor_id:=NULL;recorded_at:=now_at;RETURN NEXT;
        END $fn$
        """
    )


def _install_actor_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.add_diversion_evidence(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_evidence_id uuid,requested_clue_id uuid,requested_expected_version bigint,
          requested_idempotency_key text,requested_evidence_type text,requested_file_url text,
          requested_description text,requested_evidence_digest text
        ) RETURNS TABLE(resource_id uuid,clue_id uuid,clue_version bigint,investigation_status text,
          resolved boolean,observation_count bigint,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE clue public.diversion_clues%ROWTYPE;
        DECLARE receipt public.diversion_action_receipts%ROWTYPE;DECLARE digest_value text;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(
            requested_tenant_id,requested_auth_session_id,'channel:manage',false) authority;
          IF requested_audit_id IS NULL OR requested_evidence_id IS NULL OR requested_clue_id IS NULL
             OR requested_expected_version IS NULL OR requested_expected_version<1
             OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128
             OR requested_evidence_type NOT IN ('transfer','order','logistics','explanation','other')
             OR (NULLIF(trim(requested_file_url),'') IS NULL AND NULLIF(trim(requested_description),'') IS NULL)
             OR (requested_file_url IS NOT NULL AND length(requested_file_url)>500)
             OR (requested_description IS NOT NULL AND length(requested_description)>4000)
             OR requested_evidence_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='diversion evidence input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('diversion-idem:'||requested_tenant_id::text||':add_evidence:'||requested_idempotency_key,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_clue_id,requested_expected_version,
            requested_evidence_type,requested_file_url,requested_description,requested_evidence_digest)::text,
            'UTF8'),'sha256'),'hex');
          SELECT * INTO receipt FROM public.diversion_action_receipts r WHERE r.tenant_id=requested_tenant_id
            AND r.action='add_evidence' AND r.idempotency_key=requested_idempotency_key;
          IF FOUND THEN
            IF receipt.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='diversion evidence idempotency conflict'; END IF;
            observation_count:=receipt.observation_count;
            resource_id:=receipt.resource_id;clue_id:=receipt.clue_id;clue_version:=receipt.clue_version;
            investigation_status:=receipt.investigation_status;resolved:=receipt.resolved;replayed:=true;
            actor_id:=receipt.actor_id;recorded_at:=receipt.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO clue FROM public.diversion_clues c WHERE c.tenant_id=requested_tenant_id
            AND c.id=requested_clue_id FOR UPDATE;
          IF clue.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='diversion clue is unavailable'; END IF;
          IF clue.version<>requested_expected_version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='diversion clue version conflict'; END IF;
          INSERT INTO public.diversion_evidence(id,tenant_id,clue_id,evidence_type,source,file_url,description,
            uploaded_by,uploaded_by_account_id,evidence_digest,uploaded_at,created_at)
          VALUES(requested_evidence_id,requested_tenant_id,requested_clue_id,requested_evidence_type,'brand_ops',
            NULLIF(trim(requested_file_url),''),NULLIF(trim(requested_description),''),resolved_actor::text,
            resolved_actor,requested_evidence_digest,now_at,now_at);
          UPDATE public.diversion_clues c SET version=c.version+1,updated_at=now_at
            WHERE c.tenant_id=requested_tenant_id AND c.id=requested_clue_id RETURNING * INTO clue;
          INSERT INTO public.diversion_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,
            resource_id,clue_id,clue_version,investigation_status,resolved,observation_count,
            actor_id,audit_id,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,'add_evidence',requested_idempotency_key,digest_value,
            requested_evidence_id,clue.id,clue.version,clue.investigation_status,clue.resolved,
            clue.observation_count,resolved_actor,
            requested_audit_id,now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,
            timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,
            requested_tenant_id::text,'diversion_evidence_added','diversion_clue:'||clue.id::text,
            jsonb_build_object('evidence_id',requested_evidence_id,'version',clue.version),now_at,now_at,now_at);
          resource_id:=requested_evidence_id;clue_id:=clue.id;clue_version:=clue.version;
          investigation_status:=clue.investigation_status;resolved:=clue.resolved;
          observation_count:=clue.observation_count;replayed:=false;actor_id:=resolved_actor;
          recorded_at:=now_at;RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.transition_diversion_clue(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_clue_id uuid,requested_expected_version bigint,requested_idempotency_key text,
          requested_to_status text,requested_reason text,requested_resolution_note text
        ) RETURNS TABLE(resource_id uuid,clue_id uuid,clue_version bigint,investigation_status text,
          resolved boolean,observation_count bigint,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE clue public.diversion_clues%ROWTYPE;
        DECLARE receipt public.diversion_action_receipts%ROWTYPE;DECLARE digest_value text;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE terminal boolean;DECLARE previous_status text;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(
            requested_tenant_id,requested_auth_session_id,'channel:manage',false) authority;
          IF requested_audit_id IS NULL OR requested_clue_id IS NULL OR requested_expected_version IS NULL
             OR requested_expected_version<1 OR NULLIF(trim(requested_idempotency_key),'') IS NULL
             OR length(requested_idempotency_key)>128 OR requested_to_status NOT IN
               ('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer')
             OR NULLIF(trim(requested_reason),'') IS NULL OR length(trim(requested_reason))>500
             OR (requested_resolution_note IS NOT NULL AND length(requested_resolution_note)>4000) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='diversion transition input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('diversion-idem:'||requested_tenant_id::text||':transition:'||requested_idempotency_key,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_clue_id,requested_expected_version,
            requested_to_status,trim(requested_reason),requested_resolution_note)::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO receipt FROM public.diversion_action_receipts r WHERE r.tenant_id=requested_tenant_id
            AND r.action='transition' AND r.idempotency_key=requested_idempotency_key;
          IF FOUND THEN
            IF receipt.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='diversion transition idempotency conflict'; END IF;
            observation_count:=receipt.observation_count;
            resource_id:=receipt.resource_id;clue_id:=receipt.clue_id;clue_version:=receipt.clue_version;
            investigation_status:=receipt.investigation_status;resolved:=receipt.resolved;replayed:=true;
            actor_id:=receipt.actor_id;recorded_at:=receipt.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO clue FROM public.diversion_clues c WHERE c.tenant_id=requested_tenant_id
            AND c.id=requested_clue_id FOR UPDATE;
          IF clue.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='diversion clue is unavailable'; END IF;
          IF clue.version<>requested_expected_version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='diversion clue version conflict'; END IF;
          IF clue.investigation_status=requested_to_status OR NOT (
            (clue.investigation_status IN ('open','pending_evidence') AND requested_to_status IN
              ('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer'))
            OR (clue.investigation_status IN ('confirmed_diversion','false_positive','normal_transfer')
              AND requested_to_status='open')) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='diversion transition is illegal'; END IF;
          previous_status:=clue.investigation_status;
          terminal:=requested_to_status IN ('confirmed_diversion','false_positive','normal_transfer');
          INSERT INTO public.diversion_investigation_history(id,tenant_id,clue_id,from_status,to_status,
            changed_by,changed_by_account_id,changed_at,reason,created_at)
          VALUES(gen_random_uuid(),requested_tenant_id,clue.id,previous_status,requested_to_status,
            resolved_actor::text,resolved_actor,now_at,trim(requested_reason),now_at);
          UPDATE public.diversion_clues c SET investigation_status=requested_to_status,resolved=terminal,
            resolution_action=CASE WHEN terminal THEN requested_to_status END,
            resolution_note=CASE WHEN terminal THEN NULLIF(trim(requested_resolution_note),'') END,
            resolved_by_account_id=CASE WHEN terminal THEN resolved_actor END,
            resolved_at=CASE WHEN terminal THEN now_at END,version=c.version+1,updated_at=now_at
            WHERE c.tenant_id=requested_tenant_id AND c.id=clue.id RETURNING * INTO clue;
          INSERT INTO public.diversion_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,
            resource_id,clue_id,clue_version,investigation_status,resolved,observation_count,
            actor_id,audit_id,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,'transition',requested_idempotency_key,digest_value,
            clue.id,clue.id,clue.version,clue.investigation_status,clue.resolved,
            clue.observation_count,resolved_actor,
            requested_audit_id,now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,
            timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,
            requested_tenant_id::text,'diversion_clue_transitioned','diversion_clue:'||clue.id::text,
            jsonb_build_object('from_status',previous_status,'to_status',requested_to_status,
              'version',clue.version,'reason',trim(requested_reason)),now_at,now_at,now_at);
          resource_id:=clue.id;clue_id:=clue.id;clue_version:=clue.version;
          investigation_status:=clue.investigation_status;resolved:=clue.resolved;
          observation_count:=clue.observation_count;replayed:=false;actor_id:=resolved_actor;
          recorded_at:=now_at;RETURN NEXT;
        END $fn$
        """
    )


def upgrade() -> None:
    _install_observation()
    _install_actor_functions()
    for signature in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_ROLE}")
    if _role_exists():
        for table in (
            "diversion_clues",
            "diversion_observations",
            "diversion_evidence",
            "diversion_investigation_history",
            "diversion_action_receipts",
        ):
            op.execute(f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON public.{table} FROM {_ROLE}")
            op.execute(f"GRANT SELECT ON public.{table} TO {_ROLE}")


def downgrade() -> None:
    requested_revision = getattr(context.config.cmd_opts, "revision", None)
    crosses_channel_authority = requested_revision not in {"u7b0c1d2e3f4", "-1"}
    if crosses_channel_authority:
        channel_facts = op.get_bind().execute(sa.text("SELECT count(*) FROM channel_action_receipts")).scalar_one()
        if channel_facts:
            raise RuntimeError("u7b1 downgrade blocked: channel action receipts are immutable facts")
    facts = op.get_bind().execute(
        sa.text(
            "SELECT (SELECT count(*) FROM diversion_observations)+"
            "(SELECT count(*) FROM diversion_action_receipts)+"
            "(SELECT count(*) FROM diversion_evidence)+"
            "(SELECT count(*) FROM diversion_investigation_history)"
        )
    ).scalar_one()
    if facts:
        raise RuntimeError("u7b1 downgrade blocked: immutable diversion investigation facts exist")
    for signature in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
