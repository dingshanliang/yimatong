"""cut over consumer consent authority

Revision ID: u6e3f4a5b6c7
Revises: u6e2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6e3f4a5b6c7"
down_revision: str | None = "u6e2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GET = "get_current_consumer_consent_policy(uuid,text)"
_GRANT = "grant_consumer_consent(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text)"
_LEAD = "capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text)"
_WITHDRAW = "withdraw_consumer_consent(uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text)"

_FUNCTIONS_SQL = r"""
CREATE FUNCTION public.validate_consumer_consent_subject(
    requested_tenant_id uuid,requested_scan_event_id uuid,requested_scan_time timestamptz,
    requested_public_id text,requested_visitor_id text,requested_token_consumer_id uuid
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE subject_hash text;
BEGIN
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
       OR requested_visitor_id IS NULL OR btrim(requested_visitor_id)=''
       OR requested_public_id IS NULL OR btrim(requested_public_id)='' THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent subject denied';
    END IF;
    PERFORM 1 FROM public.tenants AS tenant WHERE tenant.id=requested_tenant_id FOR SHARE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='tenant unavailable'; END IF;
    PERFORM 1 FROM public.scan_events AS event
      JOIN public.code_items AS item ON item.tenant_id=event.tenant_id AND item.public_id=event.public_id
     WHERE event.tenant_id=requested_tenant_id AND event.id=requested_scan_event_id
       AND event.scan_time=requested_scan_time AND event.public_id=requested_public_id
       AND event.visitor_id=requested_visitor_id AND event.is_valid_visit IS TRUE
       AND item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id
     FOR SHARE OF event,item NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer scan evidence denied'; END IF;
    PERFORM 1 FROM public.anonymous_visitors AS visitor
     WHERE visitor.tenant_id=requested_tenant_id AND visitor.visitor_id=requested_visitor_id
       AND ((requested_token_consumer_id IS NULL AND visitor.consumer_id IS NULL)
         OR visitor.consumer_id=requested_token_consumer_id)
     FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer token subject denied'; END IF;
    subject_hash:=encode(digest(convert_to(requested_tenant_id::text||':'||requested_visitor_id,'UTF8'),'sha256'),'hex');
    RETURN subject_hash;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
END $f$;

CREATE FUNCTION public.get_current_consumer_consent_policy(requested_tenant_id uuid,requested_purpose text)
RETURNS TABLE(policy_id uuid,purpose text,consent_type text,policy_version text,policy_digest text,
              policy_title text,policy_content text,effective_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
BEGIN
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer policy denied';
    END IF;
    RETURN QUERY SELECT policy.id,policy.purpose::text,policy.consent_type::text,policy.policy_version::text,
        policy.policy_digest::text,policy.policy_title::text,policy.policy_content,policy.effective_at
      FROM public.consumer_consent_policy_current AS current_policy
      JOIN public.consumer_consent_policies AS policy
        ON policy.tenant_id=current_policy.tenant_id AND policy.id=current_policy.policy_id
     WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose=requested_purpose
       AND policy.effective_at<=statement_timestamp();
END $f$;

CREATE FUNCTION public.grant_consumer_consent(
    requested_tenant_id uuid,requested_consent_id uuid,requested_audit_id uuid,requested_purpose text,
    requested_expected_version text,requested_expected_digest text,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_ip_hash text,requested_user_agent text,requested_idempotency_key text
) RETURNS TABLE(consent_id uuid,status text,purpose text,policy_version text,policy_digest text,
                consumer_id uuid,granted_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record; existing_action record; existing_consent record; subject_hash text; payload_hash text;
BEGIN
    IF requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)='' OR length(requested_idempotency_key)>100
       OR requested_expected_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid consent request';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('consumer-consent-idem:'||requested_tenant_id::text||':grant:'||requested_idempotency_key,0)) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
    END IF;
    subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
        requested_scan_time,requested_public_id,requested_visitor_id,requested_token_consumer_id);
    SELECT policy.* INTO policy_row FROM public.consumer_consent_policy_current AS current_policy
      JOIN public.consumer_consent_policies AS policy
        ON policy.tenant_id=current_policy.tenant_id AND policy.id=current_policy.policy_id
     WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose=requested_purpose FOR SHARE OF current_policy,policy NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='current consent policy unavailable'; END IF;
    IF policy_row.policy_version IS DISTINCT FROM requested_expected_version
       OR policy_row.policy_digest IS DISTINCT FROM requested_expected_digest THEN
        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='consent policy is not current';
    END IF;
    payload_hash:=encode(digest(convert_to(requested_purpose||':'||requested_expected_version||':'||requested_expected_digest||':'||
        requested_scan_event_id::text||':'||requested_scan_time::text||':'||requested_public_id||':'||subject_hash||':'||
        coalesce(requested_token_consumer_id::text,''),'UTF8'),'sha256'),'hex');
    SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
     WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='grant'
       AND action_row.idempotency_key=requested_idempotency_key;
    IF FOUND THEN
        IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='consent idempotency conflict';
        END IF;
        SELECT record.* INTO existing_consent FROM public.consent_records AS record
         WHERE record.tenant_id=requested_tenant_id AND record.id=existing_action.consent_id;
        consent_id:=existing_consent.id;status:=existing_consent.status;purpose:=existing_consent.purpose;
        policy_version:=existing_consent.policy_version;policy_digest:=existing_consent.policy_digest;
        consumer_id:=existing_consent.consumer_id;granted_at:=existing_consent.granted_at;replayed:=true;
        RETURN NEXT;RETURN;
    END IF;
    INSERT INTO public.consent_records(id,tenant_id,consumer_id,consent_type,status,public_id,ip_hash,scenario,
        policy_version,user_agent,policy_id,purpose,policy_digest,visitor_subject_hash,scan_event_id,scan_event_time,
        idempotency_key,authority_version,granted_at,created_at,updated_at)
    VALUES(requested_consent_id,requested_tenant_id,requested_token_consumer_id,policy_row.consent_type,'granted',
        requested_public_id,requested_ip_hash,requested_purpose,policy_row.policy_version,left(requested_user_agent,500),
        policy_row.id,requested_purpose,policy_row.policy_digest,subject_hash,requested_scan_event_id,requested_scan_time,
        requested_idempotency_key,1,statement_timestamp(),statement_timestamp(),statement_timestamp());
    INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,idempotency_key,
        payload_hash,visitor_subject_hash,result_status)
    VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,policy_row.id,requested_token_consumer_id,
        'grant',requested_idempotency_key,payload_hash,subject_hash,'granted');
    consent_id:=requested_consent_id;status:='granted';purpose:=requested_purpose;policy_version:=policy_row.policy_version;
    policy_digest:=policy_row.policy_digest;consumer_id:=requested_token_consumer_id;granted_at:=statement_timestamp();replayed:=false;
    RETURN NEXT;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='consent identity conflict';
END $f$;

CREATE FUNCTION public.capture_consumer_lead(
    requested_tenant_id uuid,requested_consumer_id uuid,requested_audit_id uuid,requested_consent_id uuid,
    requested_scan_event_id uuid,requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_phone_hash text,requested_phone_encrypted text,requested_name text,
    requested_lead_extra jsonb,requested_idempotency_key text
) RETURNS TABLE(outcome text,consumer_id uuid,consent_id uuid,contact_suppressed boolean,
                created boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE consent_row record; policy_row record; profile_row record; existing_action record;
DECLARE subject_hash text; payload_hash text; made boolean:=false;
BEGIN
    IF requested_phone_hash !~ '^[0-9a-f]{64}$' OR requested_phone_encrypted IS NULL
       OR octet_length(requested_phone_encrypted)>8192 OR jsonb_typeof(coalesce(requested_lead_extra,'{}'))<>'object'
       OR EXISTS(SELECT 1 FROM jsonb_object_keys(coalesce(requested_lead_extra,'{}')) AS key WHERE key NOT IN ('region','intention'))
       OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)='' THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid lead request';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('consumer-consent:'||requested_tenant_id::text||':'||requested_consent_id::text,0)) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
    END IF;
    subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
        requested_scan_time,requested_public_id,requested_visitor_id,requested_token_consumer_id);
    SELECT record.* INTO consent_row FROM public.consent_records AS record
     WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='consent unavailable'; END IF;
    IF consent_row.authority_version<>1 OR consent_row.status<>'granted' OR consent_row.purpose<>'lead_capture'
       OR consent_row.visitor_subject_hash<>subject_hash OR consent_row.public_id<>requested_public_id
       OR (consent_row.consumer_id IS NOT NULL AND consent_row.consumer_id IS DISTINCT FROM requested_token_consumer_id) THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='lead consent subject denied';
    END IF;
    SELECT policy.* INTO policy_row FROM public.consumer_consent_policy_current AS current_policy
      JOIN public.consumer_consent_policies AS policy ON policy.tenant_id=current_policy.tenant_id AND policy.id=current_policy.policy_id
     WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose='lead_capture' FOR SHARE OF current_policy,policy NOWAIT;
    IF consent_row.policy_id IS DISTINCT FROM policy_row.id OR consent_row.policy_digest IS DISTINCT FROM policy_row.policy_digest THEN
        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='lead consent policy is not current';
    END IF;
    payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||requested_phone_hash||':'||
      coalesce(requested_name,'')||':'||coalesce(requested_lead_extra,'{}')::text||':'||subject_hash,'UTF8'),'sha256'),'hex');
    SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
     WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='lead_capture'
       AND action_row.idempotency_key=requested_idempotency_key;
    IF FOUND THEN
      IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='lead idempotency conflict'; END IF;
      SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
       WHERE profile.tenant_id=requested_tenant_id AND profile.id=existing_action.consumer_id;
      outcome:='captured';consumer_id:=profile_row.id;consent_id:=requested_consent_id;
      contact_suppressed:=profile_row.lead_contact_suppressed;created:=false;replayed:=true;recorded_at:=existing_action.occurred_at;
      RETURN NEXT;RETURN;
    END IF;
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.phone_hash=requested_phone_hash FOR UPDATE NOWAIT;
    IF FOUND THEN
      IF requested_token_consumer_id IS NULL OR profile_row.id IS DISTINCT FROM requested_token_consumer_id THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='lead consumer subject denied';
      END IF;
    ELSE
      INSERT INTO public.consumer_profiles(id,tenant_id,phone_hash,phone_encrypted,nickname,member_level,total_points,
          extra_data,lead_contact_suppressed,lead_consent_id,lead_scan_event_id,lead_scan_event_time,lead_captured_at)
      VALUES(requested_consumer_id,requested_tenant_id,requested_phone_hash,requested_phone_encrypted,requested_name,
          'normal',0,coalesce(requested_lead_extra,'{}'),false,requested_consent_id,requested_scan_event_id,
          requested_scan_time,statement_timestamp()) RETURNING * INTO profile_row;
      made:=true;
    END IF;
    IF NOT made THEN
      UPDATE public.consumer_profiles AS profile SET phone_encrypted=requested_phone_encrypted,
          nickname=coalesce(requested_name,profile.nickname),
          extra_data=(coalesce(profile.extra_data,'{}')::jsonb||coalesce(requested_lead_extra,'{}'::jsonb))::json,
          lead_contact_suppressed=false,lead_consent_id=requested_consent_id,lead_scan_event_id=requested_scan_event_id,
          lead_scan_event_time=requested_scan_time,lead_captured_at=statement_timestamp(),updated_at=statement_timestamp()
       WHERE profile.tenant_id=requested_tenant_id AND profile.id=profile_row.id RETURNING * INTO profile_row;
    END IF;
    UPDATE public.consent_records AS record SET consumer_id=profile_row.id,updated_at=statement_timestamp()
     WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id;
    UPDATE public.anonymous_visitors AS visitor SET consumer_id=profile_row.id,updated_at=statement_timestamp()
     WHERE visitor.tenant_id=requested_tenant_id AND visitor.visitor_id=requested_visitor_id
       AND visitor.consumer_id IS NULL;
    INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,idempotency_key,
      payload_hash,visitor_subject_hash,result_status)
    VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,consent_row.policy_id,profile_row.id,
      'lead_capture',requested_idempotency_key,payload_hash,subject_hash,'captured');
    outcome:='captured';consumer_id:=profile_row.id;consent_id:=requested_consent_id;contact_suppressed:=false;
    created:=made;replayed:=false;recorded_at:=statement_timestamp();RETURN NEXT;
END $f$;

CREATE FUNCTION public.withdraw_consumer_consent(
    requested_tenant_id uuid,requested_consent_id uuid,requested_audit_id uuid,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_idempotency_key text
) RETURNS TABLE(consent_id uuid,status text,consumer_id uuid,contact_suppressed boolean,
                withdrawn_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE consent_row record; existing_action record; subject_hash text; payload_hash text; suppressed boolean:=false;
BEGIN
    IF requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)='' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid withdrawal'; END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('consumer-consent:'||requested_tenant_id::text||':'||requested_consent_id::text,0)) THEN
      RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer consent authority is busy';
    END IF;
    subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
      requested_scan_time,requested_public_id,requested_visitor_id,requested_token_consumer_id);
    SELECT record.* INTO consent_row FROM public.consent_records AS record
     WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='consent unavailable'; END IF;
    IF consent_row.authority_version<>1 OR consent_row.visitor_subject_hash<>subject_hash
       OR consent_row.public_id<>requested_public_id OR
       (consent_row.consumer_id IS NOT NULL AND consent_row.consumer_id IS DISTINCT FROM requested_token_consumer_id) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='withdrawal subject denied';
    END IF;
    payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||subject_hash,'UTF8'),'sha256'),'hex');
    SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
     WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='withdraw'
       AND action_row.idempotency_key=requested_idempotency_key;
    IF FOUND THEN
      IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='withdraw idempotency conflict'; END IF;
      SELECT profile.lead_contact_suppressed INTO suppressed FROM public.consumer_profiles AS profile
       WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id;
      consent_id:=consent_row.id;status:=consent_row.status;consumer_id:=consent_row.consumer_id;
      contact_suppressed:=coalesce(suppressed,false);withdrawn_at:=consent_row.withdrawn_at;replayed:=true;RETURN NEXT;RETURN;
    END IF;
    IF consent_row.status='withdrawn' THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='consent already withdrawn'; END IF;
    UPDATE public.consent_records AS record SET status='withdrawn',withdrawn_at=statement_timestamp(),updated_at=statement_timestamp()
     WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id RETURNING * INTO consent_row;
    IF consent_row.purpose='lead_capture' AND consent_row.consumer_id IS NOT NULL THEN
      UPDATE public.consumer_profiles AS profile SET lead_contact_suppressed=true,phone_hash=NULL,phone_encrypted=NULL,
          nickname=NULL,extra_data=(coalesce(profile.extra_data,'{}')::jsonb-'region'-'intention')::json,
          updated_at=statement_timestamp()
       WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id
         AND profile.lead_consent_id=consent_row.id
       RETURNING profile.lead_contact_suppressed INTO suppressed;
      IF NOT FOUND THEN
        SELECT profile.lead_contact_suppressed INTO suppressed FROM public.consumer_profiles AS profile
         WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id;
      END IF;
    END IF;
    INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,idempotency_key,
      payload_hash,visitor_subject_hash,result_status)
    VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,consent_row.policy_id,consent_row.consumer_id,
      'withdraw',requested_idempotency_key,payload_hash,subject_hash,'withdrawn');
    consent_id:=consent_row.id;status:='withdrawn';consumer_id:=consent_row.consumer_id;
    contact_suppressed:=suppressed;withdrawn_at:=consent_row.withdrawn_at;replayed:=false;RETURN NEXT;
END $f$;
"""

_GUARDS_SQL = r"""
CREATE FUNCTION public.seed_consumer_consent_policies_for_tenant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record;
BEGIN
  FOR policy_row IN SELECT * FROM (VALUES
    ('privacy_policy','privacy','2026-07-27-v1','隐私政策与个人信息处理告知',
     '我们仅在提供扫码溯源、会员与权益服务所必需的范围内处理个人信息。您可随时撤回同意；撤回不影响此前处理的合法性。'),
    ('lead_capture','marketing','2026-07-27-v1','联系信息收集与营销沟通同意',
     '经您主动同意后，我们会保存您提交的联系方式、称呼、地区和意向，用于品牌咨询与后续沟通。您可随时撤回，撤回后联系信息将被抑制并删除可恢复的线索字段。'),
    ('wechat_cash_payout','data_share','2026-08-11-v1','微信现金权益发放授权',
     '经您主动同意后，我们仅为发放已领取的现金权益使用受保护的微信身份标识，并按照交易、审计和合规要求保留必要事实。')
  ) AS defaults(purpose,consent_type,policy_version,policy_title,policy_content)
  LOOP
    INSERT INTO public.consumer_consent_policies(id,tenant_id,purpose,consent_type,policy_version,
      policy_digest,policy_title,policy_content,effective_at)
    VALUES(gen_random_uuid(),NEW.id,policy_row.purpose,policy_row.consent_type,policy_row.policy_version,
      encode(digest(convert_to(policy_row.policy_content,'UTF8'),'sha256'),'hex'),policy_row.policy_title,
      policy_row.policy_content,statement_timestamp()) RETURNING * INTO policy_row;
    INSERT INTO public.consumer_consent_policy_current(tenant_id,purpose,policy_id)
    VALUES(NEW.id,policy_row.purpose,policy_row.id);
  END LOOP;
  RETURN NEW;
END $f$;

CREATE TRIGGER trg_seed_consumer_consent_policies AFTER INSERT ON public.tenants
FOR EACH ROW EXECUTE FUNCTION public.seed_consumer_consent_policies_for_tenant();

CREATE FUNCTION public.guard_immutable_consumer_consent_policy() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
BEGIN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer consent policy versions are immutable'; END $f$;

CREATE TRIGGER trg_guard_immutable_consumer_consent_policy BEFORE UPDATE OR DELETE
ON public.consumer_consent_policies FOR EACH ROW EXECUTE FUNCTION public.guard_immutable_consumer_consent_policy();

CREATE FUNCTION public.guard_consumer_lead_authority_fields() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
BEGIN
  IF current_user='yimatong_app' THEN
    IF TG_OP='INSERT' AND (NEW.phone_hash IS NOT NULL OR NEW.phone_encrypted IS NOT NULL OR NEW.nickname IS NOT NULL
       OR coalesce(NEW.extra_data,'{}')::jsonb<>'{}'::jsonb OR NEW.lead_contact_suppressed IS TRUE
       OR NEW.lead_consent_id IS NOT NULL OR NEW.lead_scan_event_id IS NOT NULL
       OR NEW.lead_scan_event_time IS NOT NULL OR NEW.lead_captured_at IS NOT NULL) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer lead fields require authority function';
    ELSIF TG_OP='UPDATE' AND (OLD.phone_hash IS DISTINCT FROM NEW.phone_hash
       OR OLD.phone_encrypted IS DISTINCT FROM NEW.phone_encrypted OR OLD.nickname IS DISTINCT FROM NEW.nickname
       OR OLD.extra_data::jsonb IS DISTINCT FROM NEW.extra_data::jsonb
       OR OLD.lead_contact_suppressed IS DISTINCT FROM NEW.lead_contact_suppressed
       OR OLD.lead_consent_id IS DISTINCT FROM NEW.lead_consent_id OR OLD.lead_scan_event_id IS DISTINCT FROM NEW.lead_scan_event_id
       OR OLD.lead_scan_event_time IS DISTINCT FROM NEW.lead_scan_event_time OR OLD.lead_captured_at IS DISTINCT FROM NEW.lead_captured_at) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer lead fields require authority function';
    END IF;
  END IF;
  RETURN NEW;
END $f$;

CREATE TRIGGER trg_guard_consumer_lead_authority_fields BEFORE INSERT OR UPDATE ON public.consumer_profiles
FOR EACH ROW EXECUTE FUNCTION public.guard_consumer_lead_authority_fields();
"""


def _role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar_one())


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.consent_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.consent_records FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS consent_records_tenant_isolation ON public.consent_records")
    op.execute("CREATE POLICY consent_records_tenant_isolation ON public.consent_records USING (tenant_id=public.current_tenant_id()) WITH CHECK (tenant_id=public.current_tenant_id())")
    for statement in [part.strip() for part in _FUNCTIONS_SQL.split("\n\n") if part.strip()]:
        op.execute(statement)
    for statement in [part.strip() for part in _GUARDS_SQL.split("\n\n") if part.strip()]:
        op.execute(statement)
    for signature in (_GET, _GRANT, _LEAD, _WITHDRAW):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.validate_consumer_consent_subject(uuid,uuid,timestamptz,text,text,uuid) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.seed_consumer_consent_policies_for_tenant() FROM PUBLIC")
    if _role_exists():
        op.execute("REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON public.consent_records FROM yimatong_app")
        for table in ("consumer_consent_policies", "consumer_consent_policy_current", "consumer_consent_actions"):
            op.execute(f"REVOKE ALL ON public.{table} FROM yimatong_app")
            op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")
        for signature in (_GET, _GRANT, _LEAD, _WITHDRAW):
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")
        op.execute("REVOKE ALL ON FUNCTION public.validate_consumer_consent_subject(uuid,uuid,timestamptz,text,text,uuid) FROM yimatong_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM consumer_consent_actions)")).scalar_one():
        raise RuntimeError("u6e3 downgrade blocked: immutable consumer consent actions exist")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_consumer_lead_authority_fields ON public.consumer_profiles")
    op.execute("DROP FUNCTION IF EXISTS public.guard_consumer_lead_authority_fields()")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_immutable_consumer_consent_policy ON public.consumer_consent_policies")
    op.execute("DROP FUNCTION IF EXISTS public.guard_immutable_consumer_consent_policy()")
    op.execute("DROP TRIGGER IF EXISTS trg_seed_consumer_consent_policies ON public.tenants")
    op.execute("DROP FUNCTION IF EXISTS public.seed_consumer_consent_policies_for_tenant()")
    for signature in (_WITHDRAW, _LEAD, _GRANT, _GET):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("DROP FUNCTION IF EXISTS public.validate_consumer_consent_subject(uuid,uuid,timestamptz,text,text,uuid)")
    if _role_exists():
        op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.consent_records TO yimatong_app")
