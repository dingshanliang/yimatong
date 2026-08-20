"""add brand membership identity

Revision ID: a20b21c22d23
Revises: 929f1ea7db75
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a20b21c22d23"
down_revision: str | Sequence[str] | None = "929f1ea7db75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "brand_memberships",
    "brand_membership_profile_links",
    "member_identity_credentials",
    "brand_membership_events",
)

_BASE_CONSENT_POLICY_VALUES = """
    ('privacy_policy','privacy','2026-07-27-v1','隐私政策与个人信息处理告知',
     '我们仅在提供扫码溯源、会员与权益服务所必需的范围内处理个人信息。您可随时撤回同意；撤回不影响此前处理的合法性。'),
    ('lead_capture','marketing','2026-07-27-v1','联系信息收集与营销沟通同意',
     '经您主动同意后，我们会保存您提交的联系方式、称呼、地区和意向，用于品牌咨询与后续沟通。您可随时撤回，撤回后联系信息将被抑制并删除可恢复的线索字段。'),
    ('wechat_cash_payout','data_share','2026-08-11-v1','微信现金权益发放授权',
     '经您主动同意后，我们仅为发放已领取的现金权益使用受保护的微信身份标识，并按照交易、审计和合规要求保留必要事实。')
"""
_MEMBERSHIP_CONSENT_POLICY_VALUE = """
    ('brand_membership','privacy','2026-08-20-v1','品牌会员服务协议与个人信息处理同意',
     '经您主动勾选同意后，我们将建立并管理您的品牌会员关系，用于识别会员身份、保存权益和提供会员服务。营销消息将另行征得同意，不因加入会员而自动授权。')
"""

_MEMBERSHIP_AUTHORITY_FUNCTIONS = r"""
CREATE FUNCTION public.create_brand_membership_authority(
  requested_tenant_id uuid,requested_consent_id uuid,requested_scan_event_id uuid,
  requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
  requested_consumer_id uuid,requested_existing_consumer boolean,
  requested_membership_id uuid,requested_membership_number text,requested_link_id uuid,
  requested_event_id uuid,requested_idempotency_key text,requested_payload_hash text
) RETURNS TABLE(membership_id uuid,membership_number text,consumer_id uuid,status text,
  joined_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
#variable_conflict use_column
DECLARE consent public.consent_records%ROWTYPE;existing_event public.brand_membership_events%ROWTYPE;
  existing_membership public.brand_memberships%ROWTYPE;existing_consumer_id uuid;
BEGIN
  IF (session_user<>'yimatong_app' AND session_user<>current_user)
     OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='brand membership authority denied';
  END IF;
  IF requested_consent_id IS NULL OR requested_scan_event_id IS NULL OR requested_scan_time IS NULL
     OR requested_public_id IS NULL OR btrim(requested_public_id)=''
     OR requested_visitor_id IS NULL OR btrim(requested_visitor_id)=''
     OR requested_consumer_id IS NULL OR requested_membership_id IS NULL OR requested_link_id IS NULL
     OR requested_event_id IS NULL OR requested_membership_number !~ '^MBR-[0-9A-F]{12}$'
     OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
     OR length(requested_idempotency_key)>100 OR requested_payload_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='brand membership request is invalid';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-membership-join:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
  SELECT * INTO existing_event FROM public.brand_membership_events AS event
   WHERE event.tenant_id=requested_tenant_id AND event.idempotency_key=requested_idempotency_key;
  IF FOUND THEN
    IF existing_event.event_type<>'joined' OR existing_event.payload_hash<>requested_payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='brand membership idempotency conflict';
    END IF;
    SELECT membership.* INTO existing_membership FROM public.brand_memberships AS membership
     WHERE membership.tenant_id=requested_tenant_id AND membership.id=existing_event.membership_id;
    SELECT link.consumer_profile_id INTO existing_consumer_id
      FROM public.brand_membership_profile_links AS link
     WHERE link.tenant_id=requested_tenant_id AND link.membership_id=existing_event.membership_id
       AND link.is_primary IS TRUE;
    IF existing_membership.id IS NULL OR existing_consumer_id IS NULL THEN
      RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='brand membership receipt is incomplete';
    END IF;
    RETURN QUERY SELECT existing_membership.id,existing_membership.membership_number::text,existing_consumer_id,
      existing_membership.status::text,existing_membership.joined_at,true;
    RETURN;
  END IF;

  SELECT * INTO consent FROM public.consent_records AS record
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id FOR UPDATE;
  IF NOT FOUND OR consent.status<>'granted' OR consent.purpose<>'brand_membership'
     OR consent.authority_version<>1 OR consent.scan_event_id<>requested_scan_event_id
     OR consent.scan_event_time<>requested_scan_time OR consent.public_id<>requested_public_id
     OR (consent.consumer_id IS NOT NULL AND consent.consumer_id<>requested_consumer_id)
     OR NOT EXISTS(
       SELECT 1 FROM public.consumer_consent_policy_current AS current_policy
        WHERE current_policy.tenant_id=requested_tenant_id
          AND current_policy.purpose='brand_membership' AND current_policy.policy_id=consent.policy_id)
     OR NOT EXISTS(
       SELECT 1 FROM public.consumer_consent_actions AS receipt
        WHERE receipt.tenant_id=requested_tenant_id AND receipt.consent_id=requested_consent_id
          AND receipt.action='grant' AND receipt.result_status='granted')
     OR requested_visitor_id IS NULL OR btrim(requested_visitor_id)='' THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='brand membership consent authority denied';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-membership-consumer:'||requested_tenant_id::text||':'||requested_consumer_id::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.consumer_profiles AS profile
      WHERE profile.tenant_id=requested_tenant_id AND profile.id=requested_consumer_id) THEN
    IF requested_existing_consumer OR consent.consumer_id IS NOT NULL THEN
      RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='brand membership consumer is missing';
    END IF;
    INSERT INTO public.consumer_profiles(id,tenant_id,member_level,total_points,extra_data,
      lead_contact_suppressed,created_at,updated_at)
    VALUES(requested_consumer_id,requested_tenant_id,'normal',0,'{}'::json,false,
      statement_timestamp(),statement_timestamp());
  END IF;
  SELECT membership.* INTO existing_membership
    FROM public.brand_memberships AS membership
    JOIN public.brand_membership_profile_links AS link
      ON link.tenant_id=membership.tenant_id AND link.membership_id=membership.id
   WHERE membership.tenant_id=requested_tenant_id AND membership.status='active'
     AND link.consumer_profile_id=requested_consumer_id FOR SHARE OF membership,link;
  IF FOUND THEN
    RETURN QUERY SELECT existing_membership.id,existing_membership.membership_number::text,requested_consumer_id,
      existing_membership.status::text,existing_membership.joined_at,true;
    RETURN;
  END IF;
  INSERT INTO public.brand_memberships(id,tenant_id,membership_number,status,join_consent_id)
  VALUES(requested_membership_id,requested_tenant_id,requested_membership_number,'active',requested_consent_id)
  RETURNING brand_memberships.joined_at INTO joined_at;
  INSERT INTO public.brand_membership_profile_links(id,tenant_id,membership_id,consumer_profile_id,
    is_primary,link_reason,verification_receipt_hash)
  VALUES(requested_link_id,requested_tenant_id,requested_membership_id,requested_consumer_id,
    true,'explicit_join',requested_payload_hash);
  INSERT INTO public.brand_membership_events(id,tenant_id,membership_id,event_type,idempotency_key,payload_hash)
  VALUES(requested_event_id,requested_tenant_id,requested_membership_id,'joined',
    requested_idempotency_key,requested_payload_hash);
  UPDATE public.consent_records AS record SET consumer_id=requested_consumer_id
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id
     AND record.consumer_id IS NULL;
  membership_id:=requested_membership_id;membership_number:=requested_membership_number;
  consumer_id:=requested_consumer_id;status:='active';replayed:=false;RETURN NEXT;
END $f$;

CREATE FUNCTION public.bind_brand_member_identity_authority(
  requested_tenant_id uuid,requested_membership_id uuid,requested_credential_id uuid,
  requested_credential_type text,requested_issuer text,requested_subject_hash text,
  requested_subject_ciphertext bytea,requested_subject_nonce bytea,requested_subject_key_id text,
  requested_verification_receipt_hash text,requested_event_id uuid,
  requested_idempotency_key text,requested_payload_hash text
) RETURNS TABLE(credential_id uuid,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
#variable_conflict use_column
DECLARE prior public.brand_membership_events%ROWTYPE;existing_credential public.member_identity_credentials%ROWTYPE;
BEGIN
  IF (session_user<>'yimatong_app' AND session_user<>current_user)
     OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member identity authority denied';
  END IF;
  IF requested_membership_id IS NULL OR requested_credential_id IS NULL OR requested_event_id IS NULL
     OR requested_credential_type NOT IN ('verified_phone','wechat_openid','wechat_unionid')
     OR requested_issuer IS NULL OR length(btrim(requested_issuer)) NOT BETWEEN 1 AND 160
     OR requested_subject_hash !~ '^[0-9a-f]{64}$'
     OR requested_verification_receipt_hash !~ '^[0-9a-f]{64}$'
     OR octet_length(requested_subject_nonce)<>12 OR octet_length(requested_subject_ciphertext)<16
     OR requested_subject_key_id !~ '^aes-master-v[1-9][0-9]*$'
     OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
     OR length(requested_idempotency_key)>100 OR requested_payload_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='member identity request is invalid';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-member-identity:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
  SELECT * INTO prior FROM public.brand_membership_events AS event
   WHERE event.tenant_id=requested_tenant_id AND event.idempotency_key=requested_idempotency_key;
  IF FOUND THEN
    IF prior.event_type<>'identity_bound' OR prior.membership_id<>requested_membership_id
       OR prior.payload_hash<>requested_payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='member identity idempotency conflict';
    END IF;
    SELECT * INTO existing_credential FROM public.member_identity_credentials AS credential
     WHERE credential.tenant_id=requested_tenant_id AND credential.membership_id=requested_membership_id
       AND credential.credential_type=requested_credential_type AND credential.issuer=requested_issuer
       AND credential.subject_hash=requested_subject_hash AND credential.revoked_at IS NULL;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='member identity receipt is incomplete'; END IF;
    RETURN QUERY SELECT existing_credential.id,true;RETURN;
  END IF;
  PERFORM 1 FROM public.brand_memberships AS membership
   WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_membership_id
     AND membership.status='active' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='active brand membership is missing'; END IF;
  SELECT * INTO existing_credential FROM public.member_identity_credentials AS credential
   WHERE credential.tenant_id=requested_tenant_id AND credential.credential_type=requested_credential_type
     AND credential.issuer=requested_issuer AND credential.subject_hash=requested_subject_hash
     AND credential.revoked_at IS NULL FOR UPDATE;
  IF FOUND THEN
    IF existing_credential.membership_id<>requested_membership_id THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='member identity already belongs to another membership';
    END IF;
    RETURN QUERY SELECT existing_credential.id,true;RETURN;
  END IF;
  INSERT INTO public.member_identity_credentials(id,tenant_id,membership_id,credential_type,issuer,
    subject_hash,subject_ciphertext,subject_nonce,subject_key_id,verification_receipt_hash)
  VALUES(requested_credential_id,requested_tenant_id,requested_membership_id,requested_credential_type,
    requested_issuer,requested_subject_hash,requested_subject_ciphertext,requested_subject_nonce,
    requested_subject_key_id,requested_verification_receipt_hash);
  INSERT INTO public.brand_membership_events(id,tenant_id,membership_id,event_type,idempotency_key,payload_hash)
  VALUES(requested_event_id,requested_tenant_id,requested_membership_id,'identity_bound',
    requested_idempotency_key,requested_payload_hash);
  credential_id:=requested_credential_id;replayed:=false;RETURN NEXT;
END $f$;

CREATE FUNCTION public.recover_brand_membership_authority(
  requested_tenant_id uuid,requested_membership_id uuid,requested_credential_id uuid,
  requested_consumer_id uuid,requested_link_id uuid,requested_event_id uuid,
  requested_token_use_key text,requested_payload_hash text
) RETURNS TABLE(membership_id uuid,membership_number text,consumer_id uuid,status text,
  joined_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
#variable_conflict use_column
DECLARE prior public.brand_membership_events%ROWTYPE;target public.brand_memberships%ROWTYPE;
  linked_membership_id uuid;
BEGIN
  IF (session_user<>'yimatong_app' AND session_user<>current_user)
     OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member recovery authority denied';
  END IF;
  IF requested_membership_id IS NULL OR requested_credential_id IS NULL OR requested_consumer_id IS NULL
     OR requested_link_id IS NULL OR requested_event_id IS NULL
     OR requested_token_use_key !~ '^recovery-token:[0-9a-f-]{36}$'
     OR length(requested_token_use_key)>100 OR requested_payload_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='member recovery request is invalid';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-member-recovery:'||requested_tenant_id::text||':'||requested_token_use_key,0));
  SELECT * INTO prior FROM public.brand_membership_events AS event
   WHERE event.tenant_id=requested_tenant_id AND event.idempotency_key=requested_token_use_key;
  IF FOUND THEN
    IF prior.event_type<>'recovered' OR prior.membership_id<>requested_membership_id
       OR prior.payload_hash<>requested_payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='member recovery token already used';
    END IF;
    SELECT * INTO target FROM public.brand_memberships AS membership
     WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_membership_id;
    RETURN QUERY SELECT target.id,target.membership_number::text,requested_consumer_id,
      target.status::text,target.joined_at,true;
    RETURN;
  END IF;
  SELECT * INTO target FROM public.brand_memberships AS membership
   WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_membership_id
     AND membership.status='active' FOR SHARE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.member_identity_credentials AS credential
      WHERE credential.tenant_id=requested_tenant_id AND credential.id=requested_credential_id
        AND credential.membership_id=requested_membership_id AND credential.revoked_at IS NULL)
     OR NOT EXISTS(SELECT 1 FROM public.consumer_profiles AS profile
      WHERE profile.tenant_id=requested_tenant_id AND profile.id=requested_consumer_id) THEN
    RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='member recovery evidence is missing';
  END IF;
  SELECT link.membership_id INTO linked_membership_id FROM public.brand_membership_profile_links AS link
   JOIN public.brand_memberships AS membership
     ON membership.tenant_id=link.tenant_id AND membership.id=link.membership_id
   WHERE link.tenant_id=requested_tenant_id AND link.consumer_profile_id=requested_consumer_id
     AND membership.status='active' FOR SHARE OF link,membership;
  IF linked_membership_id IS NOT NULL AND linked_membership_id<>requested_membership_id THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='member recovery requires two membership proofs';
  END IF;
  IF linked_membership_id IS NULL THEN
    INSERT INTO public.brand_membership_profile_links(id,tenant_id,membership_id,consumer_profile_id,
      is_primary,link_reason,verification_receipt_hash)
    VALUES(requested_link_id,requested_tenant_id,requested_membership_id,requested_consumer_id,
      false,'verified_recovery',requested_payload_hash);
  END IF;
  INSERT INTO public.brand_membership_events(id,tenant_id,membership_id,event_type,idempotency_key,payload_hash)
  VALUES(requested_event_id,requested_tenant_id,requested_membership_id,'recovered',
    requested_token_use_key,requested_payload_hash);
  RETURN QUERY SELECT target.id,target.membership_number::text,requested_consumer_id,
    target.status::text,target.joined_at,false;
END $f$;

CREATE FUNCTION public.merge_brand_memberships_authority(
  requested_tenant_id uuid,requested_source_id uuid,requested_target_id uuid,
  requested_current_consumer_id uuid,requested_source_credential_id uuid,
  requested_target_credential_id uuid,requested_source_token_key text,requested_target_token_key text,
  requested_source_event_id uuid,requested_target_event_id uuid,requested_payload_hash text,
  requested_reencrypted_credentials jsonb
) RETURNS TABLE(membership_id uuid,membership_number text,consumer_id uuid,status text,
  joined_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
#variable_conflict use_column
DECLARE prior_count integer;source public.brand_memberships%ROWTYPE;target public.brand_memberships%ROWTYPE;
  active_credential_count integer;reencrypted_count integer;credential_item record;
BEGIN
  IF (session_user<>'yimatong_app' AND session_user<>current_user)
     OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member merge authority denied';
  END IF;
  IF requested_source_id IS NULL OR requested_target_id IS NULL OR requested_source_id=requested_target_id
     OR requested_current_consumer_id IS NULL OR requested_source_credential_id IS NULL
     OR requested_target_credential_id IS NULL OR requested_source_credential_id=requested_target_credential_id
     OR requested_source_event_id IS NULL OR requested_target_event_id IS NULL
     OR requested_source_token_key !~ '^recovery-token:[0-9a-f-]{36}$'
     OR requested_target_token_key !~ '^recovery-token:[0-9a-f-]{36}$'
     OR requested_source_token_key=requested_target_token_key
     OR requested_payload_hash !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(requested_reencrypted_credentials)<>'object' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='member merge request is invalid';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-member-token:'||requested_tenant_id::text||':'||least(requested_source_token_key,requested_target_token_key),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'brand-member-token:'||requested_tenant_id::text||':'||greatest(requested_source_token_key,requested_target_token_key),0));
  SELECT count(*) INTO prior_count FROM public.brand_membership_events AS event
   WHERE event.tenant_id=requested_tenant_id
     AND event.idempotency_key IN (requested_source_token_key,requested_target_token_key);
  IF prior_count>0 THEN
    IF prior_count<>2 OR NOT EXISTS(SELECT 1 FROM public.brand_membership_events AS event
        WHERE event.tenant_id=requested_tenant_id AND event.idempotency_key=requested_source_token_key
          AND event.membership_id=requested_source_id AND event.event_type='merged_source'
          AND event.payload_hash=requested_payload_hash)
       OR NOT EXISTS(SELECT 1 FROM public.brand_membership_events AS event
        WHERE event.tenant_id=requested_tenant_id AND event.idempotency_key=requested_target_token_key
          AND event.membership_id=requested_target_id AND event.event_type='merged_target'
          AND event.payload_hash=requested_payload_hash) THEN
      RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='member recovery token already used';
    END IF;
    SELECT * INTO target FROM public.brand_memberships AS membership
     WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_target_id;
    RETURN QUERY SELECT target.id,target.membership_number::text,requested_current_consumer_id,
      target.status::text,target.joined_at,true;RETURN;
  END IF;
  PERFORM 1 FROM public.brand_memberships AS membership
   WHERE membership.tenant_id=requested_tenant_id
     AND membership.id IN (requested_source_id,requested_target_id) ORDER BY membership.id FOR UPDATE;
  SELECT * INTO source FROM public.brand_memberships AS membership
   WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_source_id;
  SELECT * INTO target FROM public.brand_memberships AS membership
   WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_target_id;
  IF source.id IS NULL OR target.id IS NULL OR source.status<>'active' OR target.status<>'active'
     OR NOT EXISTS(SELECT 1 FROM public.brand_membership_profile_links AS link
       WHERE link.tenant_id=requested_tenant_id AND link.membership_id=requested_source_id
         AND link.consumer_profile_id=requested_current_consumer_id)
     OR NOT EXISTS(SELECT 1 FROM public.member_identity_credentials AS credential
       WHERE credential.tenant_id=requested_tenant_id AND credential.id=requested_source_credential_id
         AND credential.membership_id=requested_source_id AND credential.revoked_at IS NULL)
     OR NOT EXISTS(SELECT 1 FROM public.member_identity_credentials AS credential
       WHERE credential.tenant_id=requested_tenant_id AND credential.id=requested_target_credential_id
         AND credential.membership_id=requested_target_id AND credential.revoked_at IS NULL) THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='two live member identity proofs are required';
  END IF;
  IF EXISTS(WITH RECURSIVE merge_chain(id,merged_into_id) AS (
      SELECT membership.id,membership.merged_into_id FROM public.brand_memberships AS membership
       WHERE membership.tenant_id=requested_tenant_id AND membership.id=requested_target_id
      UNION ALL
      SELECT membership.id,membership.merged_into_id FROM public.brand_memberships AS membership
      JOIN merge_chain ON membership.id=merge_chain.merged_into_id
       WHERE membership.tenant_id=requested_tenant_id)
    SELECT 1 FROM merge_chain WHERE id=requested_source_id) THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='member merge cycle denied';
  END IF;
  SELECT count(*) INTO active_credential_count FROM public.member_identity_credentials AS credential
   WHERE credential.tenant_id=requested_tenant_id AND credential.membership_id=requested_source_id
     AND credential.revoked_at IS NULL;
  SELECT count(*) INTO reencrypted_count FROM jsonb_object_keys(requested_reencrypted_credentials);
  IF active_credential_count<>reencrypted_count OR active_credential_count=0 THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='complete credential re-encryption is required';
  END IF;
  FOR credential_item IN SELECT key,value FROM jsonb_each(requested_reencrypted_credentials)
  LOOP
    IF credential_item.value->>'ciphertext_hex' !~ '^[0-9a-f]+$'
       OR length(credential_item.value->>'ciphertext_hex')<32
       OR credential_item.value->>'nonce_hex' !~ '^[0-9a-f]{24}$'
       OR credential_item.value->>'key_id' !~ '^aes-master-v[1-9][0-9]*$' THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid credential re-encryption envelope';
    END IF;
    UPDATE public.member_identity_credentials AS credential
       SET membership_id=requested_target_id,
           subject_ciphertext=decode(credential_item.value->>'ciphertext_hex','hex'),
           subject_nonce=decode(credential_item.value->>'nonce_hex','hex'),
           subject_key_id=credential_item.value->>'key_id'
     WHERE credential.tenant_id=requested_tenant_id AND credential.membership_id=requested_source_id
       AND credential.id=credential_item.key::uuid AND credential.revoked_at IS NULL;
    IF NOT FOUND THEN
      RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='credential re-encryption target is invalid';
    END IF;
  END LOOP;
  UPDATE public.brand_membership_profile_links SET membership_id=requested_target_id,is_primary=false,
    link_reason='verified_merge',verification_receipt_hash=requested_payload_hash
   WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
  UPDATE public.brand_memberships SET status='merged',merged_into_id=requested_target_id,
    updated_at=statement_timestamp()
   WHERE tenant_id=requested_tenant_id AND id=requested_source_id AND status='active';
  INSERT INTO public.brand_membership_events(id,tenant_id,membership_id,event_type,idempotency_key,payload_hash)
  VALUES(requested_source_event_id,requested_tenant_id,requested_source_id,'merged_source',
    requested_source_token_key,requested_payload_hash),
    (requested_target_event_id,requested_tenant_id,requested_target_id,'merged_target',
    requested_target_token_key,requested_payload_hash);
  RETURN QUERY SELECT target.id,target.membership_number::text,requested_current_consumer_id,
    target.status::text,target.joined_at,false;
END $f$;
"""

_MEMBERSHIP_AUTHORITY_SIGNATURES = (
    "create_brand_membership_authority(uuid,uuid,uuid,timestamptz,text,text,uuid,boolean,uuid,text,uuid,uuid,text,text)",
    "bind_brand_member_identity_authority(uuid,uuid,uuid,text,text,text,bytea,bytea,text,text,uuid,text,text)",
    "recover_brand_membership_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text)",
    "merge_brand_memberships_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb)",
)


def _seed_policy_function(*, include_membership: bool) -> str:
    values = _BASE_CONSENT_POLICY_VALUES
    if include_membership:
        values += "," + _MEMBERSHIP_CONSENT_POLICY_VALUE
    return f"""
CREATE OR REPLACE FUNCTION public.seed_consumer_consent_policies_for_tenant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record;
BEGIN
  FOR policy_row IN SELECT * FROM (VALUES {values}
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
"""


def _tenant_policy(table: str, *, append_only: bool = False) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    if append_only:
        op.execute(
            f"CREATE POLICY tenant_select ON public.{table} FOR SELECT USING (tenant_id=public.current_tenant_id())"
        )
        op.execute(
            f"CREATE POLICY tenant_insert ON public.{table} FOR INSERT "
            "WITH CHECK (tenant_id=public.current_tenant_id())"
        )
    else:
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} "
            "USING (tenant_id=public.current_tenant_id()) "
            "WITH CHECK (tenant_id=public.current_tenant_id())"
        )
    op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")


def upgrade() -> None:
    op.create_table(
        "brand_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_number", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("join_consent_id", sa.Uuid(), nullable=False),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active','merged')", name="ck_brand_memberships_status"),
        sa.CheckConstraint(
            "(status='active' AND merged_into_id IS NULL) OR (status='merged' AND merged_into_id IS NOT NULL)",
            name="ck_brand_memberships_merge_state",
        ),
        sa.CheckConstraint("merged_into_id IS NULL OR merged_into_id<>id", name="ck_brand_memberships_not_self_merged"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_memberships_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "join_consent_id"],
            ["consent_records.tenant_id", "consent_records.id"],
            name="fk_brand_memberships_join_consent",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "merged_into_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_brand_memberships_merged_into",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_brand_memberships_tenant_id"),
        sa.UniqueConstraint("tenant_id", "membership_number", name="uq_brand_memberships_tenant_number"),
    )
    op.create_index("ix_brand_memberships_tenant_joined", "brand_memberships", ["tenant_id", "joined_at"])
    op.create_index(
        "ix_brand_memberships_tenant_join_consent",
        "brand_memberships",
        ["tenant_id", "join_consent_id"],
    )
    op.create_index(
        "ix_brand_memberships_tenant_merged_into",
        "brand_memberships",
        ["tenant_id", "merged_into_id"],
        postgresql_where=sa.text("merged_into_id IS NOT NULL"),
    )

    op.create_table(
        "brand_membership_profile_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_profile_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("link_reason", sa.String(30), nullable=False),
        sa.Column("verification_receipt_hash", sa.String(64), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "link_reason IN ('explicit_join','verified_recovery','verified_merge')",
            name="ck_membership_profile_links_reason",
        ),
        sa.CheckConstraint("length(verification_receipt_hash)=64", name="ck_membership_profile_links_receipt_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_membership_profile_links_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_membership_profile_links_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "consumer_profile_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_membership_profile_links_consumer",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_membership_profile_links_tenant_id"),
        sa.UniqueConstraint("tenant_id", "consumer_profile_id", name="uq_membership_profile_links_tenant_consumer"),
    )
    op.create_index(
        "ix_membership_profile_links_tenant_member",
        "brand_membership_profile_links",
        ["tenant_id", "membership_id"],
    )
    op.create_index(
        "uq_membership_profile_links_primary",
        "brand_membership_profile_links",
        ["tenant_id", "membership_id"],
        unique=True,
        postgresql_where=sa.text("is_primary IS TRUE"),
    )

    op.create_table(
        "member_identity_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("credential_type", sa.String(30), nullable=False),
        sa.Column("issuer", sa.String(160), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("subject_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("subject_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("subject_key_id", sa.String(32), nullable=False),
        sa.Column("verification_receipt_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "credential_type IN ('verified_phone','wechat_openid','wechat_unionid')",
            name="ck_member_identity_credentials_type",
        ),
        sa.CheckConstraint("length(trim(issuer)) BETWEEN 1 AND 160", name="ck_member_identity_credentials_issuer"),
        sa.CheckConstraint("length(subject_hash)=64", name="ck_member_identity_credentials_hash"),
        sa.CheckConstraint(
            "length(verification_receipt_hash)=64",
            name="ck_member_identity_credentials_receipt_hash",
        ),
        sa.CheckConstraint(
            "length(subject_nonce)=12 AND octet_length(subject_ciphertext)>=16 "
            "AND subject_key_id ~ '^aes-master-v[1-9][0-9]*$'",
            name="ck_member_identity_credentials_envelope",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_member_identity_credentials_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_identity_credentials_membership",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_member_identity_credentials_tenant_id"),
    )
    op.create_index(
        "ix_member_identity_credentials_tenant_member",
        "member_identity_credentials",
        ["tenant_id", "membership_id"],
    )
    op.create_index(
        "uq_member_identity_credentials_active_subject",
        "member_identity_credentials",
        ["tenant_id", "credential_type", "issuer", "subject_hash"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "brand_membership_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('joined','identity_bound','recovered','merged_source','merged_target')",
            name="ck_brand_membership_events_type",
        ),
        sa.CheckConstraint("length(payload_hash)=64", name="ck_brand_membership_events_payload_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_membership_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_brand_membership_events_membership",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_brand_membership_events_tenant_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_brand_membership_events_idempotency"),
    )
    op.create_index(
        "ix_brand_membership_events_tenant_member",
        "brand_membership_events",
        ["tenant_id", "membership_id", "occurred_at"],
    )

    for table in _TABLES:
        _tenant_policy(table, append_only=table == "brand_membership_events")
    op.execute(
        """
        CREATE FUNCTION public.guard_immutable_brand_membership_event() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='brand membership events are immutable'; END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_immutable_brand_membership_event() FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER trg_guard_immutable_brand_membership_event BEFORE UPDATE OR DELETE
        ON public.brand_membership_events FOR EACH ROW
        EXECUTE FUNCTION public.guard_immutable_brand_membership_event()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_brand_membership_authority() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF TG_OP='UPDATE' AND (
            NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.membership_number IS DISTINCT FROM OLD.membership_number
            OR NEW.join_consent_id IS DISTINCT FROM OLD.join_consent_id
            OR NEW.joined_at IS DISTINCT FROM OLD.joined_at
          ) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='brand membership authority fields are immutable';
          END IF;
          IF TG_OP='INSERT' AND NOT EXISTS(
            SELECT 1 FROM public.consent_records AS consent
            JOIN public.consumer_consent_policy_current AS current_policy
              ON current_policy.tenant_id=consent.tenant_id
             AND current_policy.purpose='brand_membership'
             AND current_policy.policy_id=consent.policy_id
            WHERE consent.tenant_id=NEW.tenant_id AND consent.id=NEW.join_consent_id
              AND consent.status='granted' AND consent.purpose='brand_membership'
              AND consent.authority_version=1
              AND EXISTS(
                SELECT 1 FROM public.consumer_consent_actions AS receipt
                WHERE receipt.tenant_id=consent.tenant_id AND receipt.consent_id=consent.id
                  AND receipt.action='grant' AND receipt.result_status='granted'
              )
          ) THEN
            RAISE EXCEPTION
              USING ERRCODE='42501',MESSAGE='brand membership requires current explicit consent authority';
          END IF;
          IF TG_OP='UPDATE' AND NOT EXISTS(
            SELECT 1 FROM public.consent_records AS consent
            WHERE consent.tenant_id=NEW.tenant_id AND consent.id=NEW.join_consent_id
              AND consent.purpose='brand_membership' AND consent.authority_version=1
              AND EXISTS(
                SELECT 1 FROM public.consumer_consent_actions AS receipt
                WHERE receipt.tenant_id=consent.tenant_id AND receipt.consent_id=consent.id
                  AND receipt.action='grant' AND receipt.result_status='granted'
              )
          ) THEN
            RAISE EXCEPTION
              USING ERRCODE='42501',MESSAGE='brand membership requires historic explicit consent authority';
          END IF;
          RETURN NEW;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_brand_membership_authority() FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER trg_guard_brand_membership_authority BEFORE INSERT OR UPDATE
        ON public.brand_memberships FOR EACH ROW
        EXECUTE FUNCTION public.guard_brand_membership_authority()
        """
    )
    for index, function_sql in enumerate(_MEMBERSHIP_AUTHORITY_FUNCTIONS.strip().split("\n\nCREATE FUNCTION")):
        op.execute(function_sql if index == 0 else "CREATE FUNCTION" + function_sql)
    for signature in _MEMBERSHIP_AUTHORITY_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    op.execute(
        "DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        "REVOKE ALL ON public.brand_memberships,public.brand_membership_profile_links,"
        "public.member_identity_credentials,public.brand_membership_events FROM yimatong_app; "
        "GRANT SELECT ON public.brand_memberships,public.brand_membership_profile_links,"
        "public.member_identity_credentials,public.brand_membership_events TO yimatong_app; "
        + " ".join(
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app;"
            for signature in _MEMBERSHIP_AUTHORITY_SIGNATURES
        )
        + " END IF; END $do$"
    )
    op.execute(_seed_policy_function(include_membership=True))
    op.execute(
        f"""
        INSERT INTO public.consumer_consent_policies(
          id,tenant_id,purpose,consent_type,policy_version,policy_digest,policy_title,policy_content,effective_at
        )
        SELECT gen_random_uuid(),tenant.id,defaults.purpose,defaults.consent_type,defaults.policy_version,
          encode(digest(convert_to(defaults.policy_content,'UTF8'),'sha256'),'hex'),defaults.policy_title,
          defaults.policy_content,statement_timestamp()
        FROM public.tenants AS tenant
        CROSS JOIN (VALUES {_MEMBERSHIP_CONSENT_POLICY_VALUE})
          AS defaults(purpose,consent_type,policy_version,policy_title,policy_content)
        ON CONFLICT (tenant_id,purpose,policy_version) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO public.consumer_consent_policy_current(tenant_id,purpose,policy_id)
        SELECT policy.tenant_id,policy.purpose,policy.id
        FROM public.consumer_consent_policies AS policy
        WHERE policy.purpose='brand_membership' AND policy.policy_version='2026-08-20-v1'
        ON CONFLICT (tenant_id,purpose) DO UPDATE
        SET policy_id=EXCLUDED.policy_id,updated_at=statement_timestamp()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    facts = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM brand_memberships)"
            "+(SELECT count(*) FROM brand_membership_profile_links)"
            "+(SELECT count(*) FROM member_identity_credentials)"
            "+(SELECT count(*) FROM brand_membership_events)"
            "+(SELECT count(*) FROM consent_records WHERE purpose='brand_membership')"
        )
    ).scalar_one()
    if facts:
        raise RuntimeError("a20 downgrade blocked: brand membership consent, identity, or audit facts exist")
    op.execute(_seed_policy_function(include_membership=False))
    op.execute("DELETE FROM public.consumer_consent_policy_current WHERE purpose='brand_membership'")
    op.execute("DROP TRIGGER trg_guard_immutable_consumer_consent_policy ON public.consumer_consent_policies")
    op.execute("DELETE FROM public.consumer_consent_policies WHERE purpose='brand_membership'")
    op.execute(
        """
        CREATE TRIGGER trg_guard_immutable_consumer_consent_policy BEFORE UPDATE OR DELETE
        ON public.consumer_consent_policies FOR EACH ROW
        EXECUTE FUNCTION public.guard_immutable_consumer_consent_policy()
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_guard_brand_membership_authority ON public.brand_memberships")
    op.execute("DROP FUNCTION IF EXISTS public.guard_brand_membership_authority()")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_immutable_brand_membership_event ON public.brand_membership_events")
    op.execute("DROP FUNCTION IF EXISTS public.guard_immutable_brand_membership_event()")
    for signature in reversed(_MEMBERSHIP_AUTHORITY_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_select ON public.{table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_insert ON public.{table}")
        op.drop_table(table)
