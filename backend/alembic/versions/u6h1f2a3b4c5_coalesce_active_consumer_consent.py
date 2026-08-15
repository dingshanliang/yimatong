"""Coalesce active consent receipts and permit bound-subject status recovery.

Revision ID: u6h1f2a3b4c5
Revises: u6h0e1f2a3b4
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6h1f2a3b4c5"
down_revision: str | None = "u6h0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_GRANT = "grant_consumer_consent(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text)"
_GRANT_BASE = (
    "grant_consumer_consent_without_subject_coalescing"
    "(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text)"
)
_STATUS = "get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid)"
_STATUS_BASE = (
    "get_consumer_consent_receipt_status_without_bound_upgrade"
    "(uuid,uuid,uuid,timestamptz,text,text,uuid)"
)
_CAPTURE = (
    "capture_consumer_lead"
    "(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text)"
)
_CAPTURE_BASE = (
    "capture_consumer_lead_without_bound_regrant"
    "(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text)"
)

_GRANT_SQL = r"""
CREATE FUNCTION public.grant_consumer_consent(
    requested_tenant_id uuid,requested_consent_id uuid,requested_audit_id uuid,requested_purpose text,
    requested_expected_version text,requested_expected_digest text,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_ip_hash text,requested_user_agent text,requested_idempotency_key text
) RETURNS TABLE(consent_id uuid,status text,purpose text,policy_version text,policy_digest text,
                consumer_id uuid,granted_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record;existing_action record;existing_consent record;subject_hash text;payload_hash text;
BEGIN
    IF requested_consent_id IS NULL OR requested_audit_id IS NULL
       OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
       OR length(requested_idempotency_key)>100 OR requested_expected_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid consent request';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
        'consumer-consent-idem:'||requested_tenant_id::text||':grant:'||requested_idempotency_key,0)) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
    END IF;
    subject_hash:=encode(public.digest(convert_to(
        requested_tenant_id::text||':'||requested_visitor_id,'UTF8'),'sha256'),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'consumer-consent-subject:'||requested_tenant_id::text||':'||requested_purpose||':'||subject_hash,0));
    IF public.validate_consumer_consent_subject(
        requested_tenant_id,requested_scan_event_id,requested_scan_time,
        requested_public_id,requested_visitor_id,requested_token_consumer_id
    ) IS DISTINCT FROM subject_hash THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent subject denied';
    END IF;
    SELECT policy.* INTO policy_row FROM public.consumer_consent_policy_current AS current_policy
      JOIN public.consumer_consent_policies AS policy
        ON policy.tenant_id=current_policy.tenant_id AND policy.id=current_policy.policy_id
     WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose=requested_purpose
     FOR SHARE OF current_policy,policy NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='current consent policy unavailable'; END IF;
    IF policy_row.policy_version IS DISTINCT FROM requested_expected_version
       OR policy_row.policy_digest IS DISTINCT FROM requested_expected_digest THEN
        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='consent policy is not current';
    END IF;
    payload_hash:=encode(public.digest(convert_to(
        requested_purpose||':'||requested_expected_version||':'||requested_expected_digest||':'||
        requested_scan_event_id::text||':'||requested_scan_time::text||':'||requested_public_id||':'||
        subject_hash||':'||coalesce(requested_token_consumer_id::text,''),'UTF8'),'sha256'),'hex');
    SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
     WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='grant'
       AND action_row.idempotency_key=requested_idempotency_key;
    IF FOUND THEN
        IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='consent idempotency conflict';
        END IF;
        SELECT record.* INTO existing_consent FROM public.consent_records AS record
         WHERE record.tenant_id=requested_tenant_id AND record.id=existing_action.consent_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='consent receipt unavailable'; END IF;
        consent_id:=existing_consent.id;status:=existing_consent.status;purpose:=existing_consent.purpose;
        policy_version:=existing_consent.policy_version;policy_digest:=existing_consent.policy_digest;
        consumer_id:=existing_consent.consumer_id;granted_at:=existing_consent.granted_at;replayed:=true;
        RETURN NEXT;RETURN;
    END IF;
    SELECT record.* INTO existing_consent FROM public.consent_records AS record
     WHERE record.tenant_id=requested_tenant_id AND record.purpose=requested_purpose
       AND record.visitor_subject_hash=subject_hash AND record.policy_id=policy_row.id
       AND record.authority_version=1 AND record.status='granted'
     FOR UPDATE NOWAIT;
    IF FOUND THEN
        IF existing_consent.consumer_id IS NOT NULL
           AND existing_consent.consumer_id IS DISTINCT FROM requested_token_consumer_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='active consent consumer subject denied';
        END IF;
        INSERT INTO public.consumer_consent_actions(
            id,tenant_id,consent_id,policy_id,consumer_id,action,idempotency_key,
            payload_hash,visitor_subject_hash,result_status
        ) VALUES (
            requested_audit_id,requested_tenant_id,existing_consent.id,policy_row.id,
            existing_consent.consumer_id,'grant',requested_idempotency_key,payload_hash,subject_hash,'granted'
        );
        consent_id:=existing_consent.id;status:=existing_consent.status;purpose:=existing_consent.purpose;
        policy_version:=existing_consent.policy_version;policy_digest:=existing_consent.policy_digest;
        consumer_id:=existing_consent.consumer_id;granted_at:=existing_consent.granted_at;replayed:=true;
        RETURN NEXT;RETURN;
    END IF;
    INSERT INTO public.consent_records(
        id,tenant_id,consumer_id,consent_type,status,public_id,ip_hash,scenario,policy_version,user_agent,
        policy_id,purpose,policy_digest,visitor_subject_hash,scan_event_id,scan_event_time,idempotency_key,
        authority_version,granted_at,created_at,updated_at
    ) VALUES (
        requested_consent_id,requested_tenant_id,requested_token_consumer_id,policy_row.consent_type,'granted',
        requested_public_id,requested_ip_hash,requested_purpose,policy_row.policy_version,left(requested_user_agent,500),
        policy_row.id,requested_purpose,policy_row.policy_digest,subject_hash,requested_scan_event_id,
        requested_scan_time,requested_idempotency_key,1,statement_timestamp(),statement_timestamp(),statement_timestamp()
    ) RETURNING * INTO existing_consent;
    INSERT INTO public.consumer_consent_actions(
        id,tenant_id,consent_id,policy_id,consumer_id,action,idempotency_key,
        payload_hash,visitor_subject_hash,result_status
    ) VALUES (
        requested_audit_id,requested_tenant_id,requested_consent_id,policy_row.id,requested_token_consumer_id,
        'grant',requested_idempotency_key,payload_hash,subject_hash,'granted'
    );
    consent_id:=existing_consent.id;status:=existing_consent.status;purpose:=existing_consent.purpose;
    policy_version:=existing_consent.policy_version;policy_digest:=existing_consent.policy_digest;
    consumer_id:=existing_consent.consumer_id;granted_at:=existing_consent.granted_at;replayed:=false;
    RETURN NEXT;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='consent identity conflict';
WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
END $f$
"""

_STATUS_SQL = r"""
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
       AND (receipt.consumer_id IS NULL OR receipt.consumer_id=requested_token_consumer_id)
     FOR SHARE OF receipt NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent receipt denied';
    END IF;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent receipt is busy';
END $f$
"""

_CAPTURE_SQL = r"""
CREATE FUNCTION public.capture_consumer_lead(
    requested_tenant_id uuid,requested_consumer_id uuid,requested_audit_id uuid,requested_consent_id uuid,
    requested_scan_event_id uuid,requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_phone_hash text,requested_phone_ciphertext bytea,
    requested_phone_nonce bytea,requested_phone_key_id text,requested_name text,
    requested_lead_extra jsonb,requested_idempotency_key text
) RETURNS TABLE(outcome text,consumer_id uuid,consent_id uuid,contact_suppressed boolean,
                created boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE consent_row record;policy_row record;profile_row record;existing_action record;
DECLARE subject_hash text;payload_hash text;made boolean:=false;
BEGIN
  IF requested_phone_hash !~ '^[0-9a-f]{64}$' OR octet_length(requested_phone_ciphertext)<>27
     OR octet_length(requested_phone_nonce)<>12 OR requested_phone_key_id !~ '^aes-master-v[1-9][0-9]*$'
     OR NOT EXISTS(SELECT 1 FROM public.consumer_phone_encryption_keys AS key
                    WHERE key.key_id=requested_phone_key_id AND key.algorithm='aes-256-gcm' AND key.status='active')
     OR jsonb_typeof(coalesce(requested_lead_extra,'{}'))<>'object'
     OR EXISTS(SELECT 1 FROM jsonb_object_keys(coalesce(requested_lead_extra,'{}')) AS key
                WHERE key NOT IN ('region','intention'))
     OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
     OR (requested_token_consumer_id IS NOT NULL
         AND requested_consumer_id IS DISTINCT FROM requested_token_consumer_id) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid lead request';
  END IF;
  IF NOT pg_try_advisory_xact_lock(hashtextextended(
      'consumer-consent:'||requested_tenant_id::text||':'||requested_consent_id::text,0)) THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
  END IF;
  subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
      requested_scan_time,requested_public_id,requested_visitor_id,requested_token_consumer_id);
  SELECT record.* INTO consent_row FROM public.consent_records AS record
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id FOR UPDATE NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='consent unavailable'; END IF;
  IF consent_row.authority_version<>1 OR consent_row.status<>'granted' OR consent_row.purpose<>'lead_capture'
     OR consent_row.visitor_subject_hash<>subject_hash OR consent_row.public_id<>requested_public_id
     OR (consent_row.consumer_id IS NOT NULL
         AND consent_row.consumer_id IS DISTINCT FROM requested_token_consumer_id) THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='lead consent subject denied';
  END IF;
  SELECT policy.* INTO policy_row FROM public.consumer_consent_policy_current AS current_policy
    JOIN public.consumer_consent_policies AS policy
      ON policy.tenant_id=current_policy.tenant_id AND policy.purpose=current_policy.purpose
     AND policy.id=current_policy.policy_id
   WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose='lead_capture'
   FOR SHARE OF current_policy,policy NOWAIT;
  IF consent_row.policy_id IS DISTINCT FROM policy_row.id
     OR consent_row.policy_digest IS DISTINCT FROM policy_row.policy_digest THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='lead consent policy is not current';
  END IF;
  payload_hash:=encode(public.digest(convert_to(requested_consent_id::text||':'||requested_phone_hash||':'||
      coalesce(requested_name,'')||':'||coalesce(requested_lead_extra,'{}')::text||':'||subject_hash,
      'UTF8'),'sha256'),'hex');
  SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
   WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='lead_capture'
     AND action_row.idempotency_key=requested_idempotency_key;
  IF FOUND THEN
    IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='lead idempotency conflict';
    END IF;
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=existing_action.consumer_id;
    outcome:='captured';consumer_id:=profile_row.id;consent_id:=requested_consent_id;
    contact_suppressed:=profile_row.lead_contact_suppressed;created:=false;replayed:=true;
    recorded_at:=existing_action.occurred_at;RETURN NEXT;RETURN;
  END IF;
  IF requested_token_consumer_id IS NOT NULL THEN
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=requested_token_consumer_id
     FOR UPDATE NOWAIT;
    IF NOT FOUND THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='bound lead consumer is unavailable';
    END IF;
    PERFORM profile.id FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.phone_hash=requested_phone_hash
       AND profile.id<>requested_token_consumer_id FOR UPDATE NOWAIT;
    IF FOUND THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='lead phone belongs to another consumer';
    END IF;
  ELSE
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.phone_hash=requested_phone_hash
     FOR UPDATE NOWAIT;
    IF FOUND THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='lead consumer subject denied';
    END IF;
  END IF;
  IF profile_row.id IS NULL THEN
    INSERT INTO public.consumer_profiles(id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,
        nickname,member_level,total_points,extra_data,lead_contact_suppressed,lead_consent_id,
        lead_scan_event_id,lead_scan_event_time,lead_captured_at)
    VALUES(requested_consumer_id,requested_tenant_id,requested_phone_hash,requested_phone_ciphertext,
        requested_phone_nonce,requested_phone_key_id,requested_name,'normal',0,coalesce(requested_lead_extra,'{}'),
        false,requested_consent_id,requested_scan_event_id,requested_scan_time,statement_timestamp())
    RETURNING * INTO profile_row;made:=true;
  ELSE
    UPDATE public.consumer_profiles AS profile SET phone_hash=requested_phone_hash,
      phone_ciphertext=requested_phone_ciphertext,phone_nonce=requested_phone_nonce,
      phone_key_id=requested_phone_key_id,nickname=coalesce(requested_name,profile.nickname),
      extra_data=(coalesce(profile.extra_data,'{}')::jsonb||coalesce(requested_lead_extra,'{}'::jsonb))::json,
      lead_contact_suppressed=false,lead_consent_id=requested_consent_id,
      lead_scan_event_id=requested_scan_event_id,lead_scan_event_time=requested_scan_time,
      lead_captured_at=statement_timestamp(),updated_at=statement_timestamp()
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=profile_row.id RETURNING * INTO profile_row;
  END IF;
  UPDATE public.consent_records AS record SET consumer_id=profile_row.id,updated_at=statement_timestamp()
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id;
  UPDATE public.anonymous_visitors AS visitor SET consumer_id=profile_row.id,updated_at=statement_timestamp()
   WHERE visitor.tenant_id=requested_tenant_id AND visitor.visitor_id=requested_visitor_id
     AND visitor.consumer_id IS NULL;
  INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,
      idempotency_key,payload_hash,visitor_subject_hash,result_status)
  VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,consent_row.policy_id,profile_row.id,
      'lead_capture',requested_idempotency_key,payload_hash,subject_hash,'captured');
  outcome:='captured';consumer_id:=profile_row.id;consent_id:=requested_consent_id;
  contact_suppressed:=false;created:=made;replayed:=false;recorded_at:=statement_timestamp();RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer lead is busy';
END $f$
"""


def _role_exists() -> bool:
    return bool(op.get_bind().exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname='yimatong_app'").scalar())


def _assert_catalog() -> None:
    for signature in (_GRANT, _GRANT_BASE, _STATUS, _STATUS_BASE, _CAPTURE, _CAPTURE_BASE):
        row = op.get_bind().execute(
            sa.text(
                "SELECT procedure.prosecdef,procedure.proconfig "
                "FROM pg_proc AS procedure WHERE procedure.oid=to_regprocedure(:signature)"
            ),
            {"signature": f"public.{signature}"},
        ).one_or_none()
        if row is None or row[0] is not True or row[1] != ["search_path=pg_catalog, public"]:
            raise RuntimeError(f"consumer consent function public.{signature} is not exact and secure")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "ALTER FUNCTION public.grant_consumer_consent"
        "(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text) "
        "RENAME TO grant_consumer_consent_without_subject_coalescing"
    )
    op.execute(
        "ALTER FUNCTION public.get_consumer_consent_receipt_status"
        "(uuid,uuid,uuid,timestamptz,text,text,uuid) "
        "RENAME TO get_consumer_consent_receipt_status_without_bound_upgrade"
    )
    op.execute(
        "ALTER FUNCTION public.capture_consumer_lead"
        "(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text) "
        "RENAME TO capture_consumer_lead_without_bound_regrant"
    )
    op.execute(_GRANT_SQL)
    op.execute(_STATUS_SQL)
    op.execute(_CAPTURE_SQL)
    _assert_catalog()
    for signature in (_GRANT_BASE, _STATUS_BASE, _CAPTURE_BASE):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {_RUNTIME_ROLE}")
    for signature in (_GRANT, _STATUS, _CAPTURE):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION public.{_GRANT}")
    op.execute(f"DROP FUNCTION public.{_STATUS}")
    op.execute(f"DROP FUNCTION public.{_CAPTURE}")
    op.execute(
        "ALTER FUNCTION public.grant_consumer_consent_without_subject_coalescing"
        "(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text) "
        "RENAME TO grant_consumer_consent"
    )
    op.execute(
        "ALTER FUNCTION public.get_consumer_consent_receipt_status_without_bound_upgrade"
        "(uuid,uuid,uuid,timestamptz,text,text,uuid) "
        "RENAME TO get_consumer_consent_receipt_status"
    )
    op.execute(
        "ALTER FUNCTION public.capture_consumer_lead_without_bound_regrant"
        "(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text) "
        "RENAME TO capture_consumer_lead"
    )
    for signature in (_GRANT, _STATUS, _CAPTURE):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")
