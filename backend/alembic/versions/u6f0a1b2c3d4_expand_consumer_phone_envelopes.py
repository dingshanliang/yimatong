"""expand consumer phone envelopes and OAuth callback authority

Revision ID: u6f0a1b2c3d4
Revises: u6e4a5b6c7d8
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6f0a1b2c3d4"
down_revision: str | None = "u6e4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEAD_V1 = "capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text)"
_LEAD_V2 = (
    "capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text)"
)
_OAUTH_BIND = (
    "bind_wechat_oauth_consumer(uuid,uuid,uuid,timestamptz,text,text,uuid,uuid,text,bytea,bytea,text,uuid,uuid)"
)


def _role_exists(role: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role})
        .scalar_one()
    )


def _configured_active_key_id() -> str:
    from app.utils.crypto import EnvKeyProvider, encrypt_bytes, init_crypto

    init_crypto(EnvKeyProvider())
    _ciphertext, _nonce, key_id = encrypt_bytes(b"", aad=b"consumer-phone-key-registry-v1")
    return key_id


def _restore_legacy_phones() -> None:
    from app.utils.crypto import (
        EnvKeyProvider,
        decrypt_consumer_phone,
        encrypt_phone,
        hash_phone,
        init_crypto,
    )

    init_crypto(EnvKeyProvider())
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id "
                "FROM public.consumer_profiles WHERE phone_hash IS NOT NULL ORDER BY tenant_id,id FOR UPDATE"
            )
        )
        .mappings()
    )
    for row in rows:
        tenant_id = uuid.UUID(str(row["tenant_id"]))
        consumer_id = uuid.UUID(str(row["id"]))
        phone = decrypt_consumer_phone(
            tenant_id,
            consumer_id,
            bytes(row["phone_ciphertext"] or b""),
            bytes(row["phone_nonce"] or b""),
            str(row["phone_key_id"] or ""),
        )
        if hash_phone(phone) != row["phone_hash"]:
            raise RuntimeError(f"consumer phone rollback digest drift for consumer_id={consumer_id}")
        op.get_bind().execute(
            sa.text(
                "UPDATE public.consumer_profiles SET phone_encrypted=:legacy "
                "WHERE tenant_id=:tenant_id AND id=:consumer_id"
            ),
            {"legacy": encrypt_phone(phone), "tenant_id": tenant_id, "consumer_id": consumer_id},
        )


_LEAD_V2_SQL = r"""
CREATE FUNCTION public.capture_consumer_lead(
    requested_tenant_id uuid,requested_consumer_id uuid,requested_audit_id uuid,requested_consent_id uuid,
    requested_scan_event_id uuid,requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_phone_hash text,requested_phone_ciphertext bytea,
    requested_phone_nonce bytea,requested_phone_key_id text,requested_name text,
    requested_lead_extra jsonb,requested_idempotency_key text
) RETURNS TABLE(outcome text,consumer_id uuid,consent_id uuid,contact_suppressed boolean,
                created boolean,replayed boolean,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE lead_result record;
BEGIN
  IF requested_phone_hash !~ '^[0-9a-f]{64}$'
     OR octet_length(requested_phone_ciphertext)<>27 OR octet_length(requested_phone_nonce)<>12
     OR requested_phone_key_id !~ '^aes-master-v[1-9][0-9]*$'
     OR NOT EXISTS(SELECT 1 FROM public.consumer_phone_encryption_keys AS key
                    WHERE key.key_id=requested_phone_key_id AND key.algorithm='aes-256-gcm' AND key.status='active') THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid consumer phone envelope';
  END IF;
  SELECT * INTO lead_result FROM public.capture_consumer_lead(
    requested_tenant_id,requested_consumer_id,requested_audit_id,requested_consent_id,
    requested_scan_event_id,requested_scan_time,requested_public_id,requested_visitor_id,
    requested_token_consumer_id,requested_phone_hash,'v2-envelope:'||requested_phone_key_id,
    requested_name,requested_lead_extra,requested_idempotency_key
  );
  IF NOT lead_result.replayed THEN
    UPDATE public.consumer_profiles AS profile
       SET phone_ciphertext=requested_phone_ciphertext,phone_nonce=requested_phone_nonce,
           phone_key_id=requested_phone_key_id,updated_at=statement_timestamp()
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=lead_result.consumer_id;
  END IF;
  outcome:=lead_result.outcome;consumer_id:=lead_result.consumer_id;consent_id:=lead_result.consent_id;
  contact_suppressed:=lead_result.contact_suppressed;created:=lead_result.created;
  replayed:=lead_result.replayed;recorded_at:=lead_result.recorded_at;RETURN NEXT;
END $f$;
"""


_OAUTH_BIND_SQL = r"""
CREATE FUNCTION public.bind_wechat_oauth_consumer(
    requested_tenant_id uuid,requested_consent_id uuid,requested_scan_event_id uuid,
    requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
    requested_token_consumer_id uuid,requested_consumer_id uuid,requested_openid_hash text,
    requested_openid_ciphertext bytea,requested_openid_nonce bytea,requested_openid_key_id text,
    requested_audit_id uuid,requested_benefit_id uuid
) RETURNS TABLE(outcome text,consumer_id uuid,consent_id uuid,bound_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE consent_row record;policy_row record;profile_row record;existing_action record;
DECLARE subject_hash text;payload_hash text;now_at timestamptz:=statement_timestamp();made boolean:=false;
BEGIN
  IF requested_consumer_id IS NULL OR requested_audit_id IS NULL OR requested_benefit_id IS NULL
     OR requested_openid_hash !~ '^[0-9a-f]{64}$' OR octet_length(requested_openid_ciphertext)<16
     OR octet_length(requested_openid_nonce)<>12 OR requested_openid_key_id !~ '^aes-master-v[1-9][0-9]*$'
     OR NOT EXISTS(SELECT 1 FROM public.consumer_phone_encryption_keys AS key
                    WHERE key.key_id=requested_openid_key_id AND key.algorithm='aes-256-gcm' AND key.status='active') THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid OAuth consumer envelope';
  END IF;
  PERFORM 1 FROM public.benefits AS benefit
   WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id
     AND benefit.benefit_type='cash_red_packet' AND benefit.status='active' FOR SHARE NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth benefit denied'; END IF;
  IF NOT pg_try_advisory_xact_lock(hashtextextended(
      'consumer-consent:'||requested_tenant_id::text||':'||requested_consent_id::text,0)) THEN
    RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer OAuth binding is busy';
  END IF;
  SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
   WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='wechat_bind'
     AND action_row.idempotency_key='wechat-bind:'||requested_consent_id::text;
  IF FOUND THEN
    IF requested_token_consumer_id IS NOT NULL
       AND requested_token_consumer_id IS DISTINCT FROM existing_action.consumer_id THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth consumer subject denied';
    END IF;
    subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
        requested_scan_time,requested_public_id,requested_visitor_id,existing_action.consumer_id);
    payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||requested_benefit_id::text||':'||
        requested_openid_hash||':'||subject_hash,'UTF8'),'sha256'),'hex');
    IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='OAuth binding conflict';
    END IF;
    outcome:='bound';consumer_id:=existing_action.consumer_id;consent_id:=requested_consent_id;
    bound_at:=existing_action.occurred_at;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  subject_hash:=public.validate_consumer_consent_subject(requested_tenant_id,requested_scan_event_id,
      requested_scan_time,requested_public_id,requested_visitor_id,requested_token_consumer_id);
  SELECT record.* INTO consent_row FROM public.consent_records AS record
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id FOR UPDATE NOWAIT;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth consent subject denied';
  END IF;
  IF consent_row.authority_version<>1 OR consent_row.status<>'granted'
     OR consent_row.purpose<>'wechat_cash_payout' OR consent_row.scenario<>'wechat_cash_payout'
     OR consent_row.visitor_subject_hash<>subject_hash OR consent_row.public_id<>requested_public_id
     OR consent_row.consumer_id IS DISTINCT FROM requested_token_consumer_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth consent subject denied';
  END IF;
  SELECT policy.* INTO policy_row FROM public.consumer_consent_policy_current AS current_policy
    JOIN public.consumer_consent_policies AS policy
      ON policy.tenant_id=current_policy.tenant_id AND policy.purpose=current_policy.purpose
     AND policy.id=current_policy.policy_id
   WHERE current_policy.tenant_id=requested_tenant_id AND current_policy.purpose='wechat_cash_payout'
   FOR SHARE OF current_policy,policy NOWAIT;
  IF NOT FOUND OR consent_row.policy_id IS DISTINCT FROM policy_row.id
     OR consent_row.policy_digest IS DISTINCT FROM policy_row.policy_digest THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='OAuth consent policy is not current';
  END IF;
  payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||requested_benefit_id::text||':'||
      requested_openid_hash||':'||subject_hash,'UTF8'),'sha256'),'hex');
  IF requested_token_consumer_id IS NOT NULL THEN
    IF requested_consumer_id IS DISTINCT FROM requested_token_consumer_id THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth consumer subject denied';
    END IF;
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=requested_token_consumer_id FOR UPDATE NOWAIT;
    IF NOT FOUND OR (profile_row.wechat_openid_hash IS NOT NULL
                     AND profile_row.wechat_openid_hash IS DISTINCT FROM requested_openid_hash) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth consumer subject denied';
    END IF;
  ELSE
    SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.wechat_openid_hash=requested_openid_hash FOR UPDATE NOWAIT;
    IF NOT FOUND THEN
      INSERT INTO public.consumer_profiles(id,tenant_id,wechat_openid_hash,wechat_openid_ciphertext,
          wechat_openid_nonce,wechat_openid_key_id,member_level,total_points)
      VALUES(requested_consumer_id,requested_tenant_id,requested_openid_hash,requested_openid_ciphertext,
          requested_openid_nonce,requested_openid_key_id,'normal',0) RETURNING * INTO profile_row;
      made:=true;
    END IF;
  END IF;
  IF NOT made AND profile_row.wechat_openid_hash IS NULL THEN
    UPDATE public.consumer_profiles AS profile SET wechat_openid_hash=requested_openid_hash,
      wechat_openid_ciphertext=requested_openid_ciphertext,wechat_openid_nonce=requested_openid_nonce,
      wechat_openid_key_id=requested_openid_key_id,updated_at=now_at
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=profile_row.id RETURNING * INTO profile_row;
  END IF;
  UPDATE public.consent_records AS record SET consumer_id=profile_row.id,updated_at=now_at
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id;
  UPDATE public.anonymous_visitors AS visitor SET consumer_id=profile_row.id,updated_at=now_at
   WHERE visitor.tenant_id=requested_tenant_id AND visitor.visitor_id=requested_visitor_id
     AND visitor.consumer_id IS NOT DISTINCT FROM requested_token_consumer_id;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='OAuth visitor subject denied'; END IF;
  INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,
      idempotency_key,payload_hash,visitor_subject_hash,result_status)
  VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,consent_row.policy_id,profile_row.id,
      'wechat_bind','wechat-bind:'||requested_consent_id::text,payload_hash,subject_hash,'bound');
  INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,
      timestamp,created_at,updated_at)
  VALUES(requested_audit_id,profile_row.id::text,requested_tenant_id::text,'consumer_wechat_bound',
      'consumer_profile:'||profile_row.id::text,
      jsonb_build_object('benefit_id',requested_benefit_id::text,'public_id',requested_public_id,
          'consent_id',requested_consent_id::text),now_at,now_at,now_at);
  outcome:='bound';consumer_id:=profile_row.id;consent_id:=requested_consent_id;
  bound_at:=now_at;replayed:=false;RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='consumer OAuth binding is busy';
END $f$;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_table(
        "consumer_phone_encryption_keys",
        sa.Column("key_id", sa.String(32), nullable=False),
        sa.Column("algorithm", sa.String(20), nullable=False, server_default="aes-256-gcm"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("algorithm='aes-256-gcm'", name="ck_consumer_phone_keys_algorithm"),
        sa.CheckConstraint("status IN ('active','retired')", name="ck_consumer_phone_keys_status"),
        sa.CheckConstraint(
            "(status='active' AND retired_at IS NULL) OR (status='retired' AND retired_at IS NOT NULL)",
            name="ck_consumer_phone_keys_retirement",
        ),
        sa.PrimaryKeyConstraint("key_id"),
    )
    op.create_index(
        "uq_consumer_phone_keys_single_active",
        "consumer_phone_encryption_keys",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status='active'"),
    )
    active_key_id = _configured_active_key_id()
    op.execute(
        sa.text(
            "INSERT INTO public.consumer_phone_encryption_keys(key_id,algorithm,status) "
            "VALUES(:key_id,'aes-256-gcm','active')"
        ).bindparams(key_id=active_key_id)
    )
    op.add_column("consumer_profiles", sa.Column("phone_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("phone_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("phone_key_id", sa.String(32), nullable=True))
    op.execute(
        "ALTER TABLE public.consumer_profiles ADD CONSTRAINT ck_consumer_profiles_phone_envelope "
        "CHECK ((phone_hash IS NULL AND phone_ciphertext IS NULL AND phone_nonce IS NULL AND phone_key_id IS NULL) OR "
        "(length(phone_hash)=64 AND phone_ciphertext IS NOT NULL AND length(phone_nonce)=12 "
        "AND NULLIF(trim(phone_key_id),'') IS NOT NULL)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.consumer_profiles ADD CONSTRAINT ck_consumer_profiles_phone_envelope_format "
        "CHECK (phone_hash IS NULL OR (phone_hash ~ '^[0-9a-f]{64}$' AND octet_length(phone_ciphertext)=27 "
        "AND phone_key_id ~ '^aes-master-v[1-9][0-9]*$')) NOT VALID"
    )
    op.create_unique_constraint(
        "uq_consumer_consent_policies_tenant_purpose_id",
        "consumer_consent_policies",
        ["tenant_id", "purpose", "id"],
    )
    op.execute(
        "ALTER TABLE public.consumer_consent_policy_current ADD CONSTRAINT "
        "fk_consumer_consent_policy_current_policy_purpose FOREIGN KEY(tenant_id,purpose,policy_id) "
        "REFERENCES public.consumer_consent_policies(tenant_id,purpose,id) NOT VALID"
    )
    op.execute("ALTER TABLE public.consumer_consent_actions DROP CONSTRAINT ck_consumer_consent_actions_action")
    op.execute(
        "ALTER TABLE public.consumer_consent_actions ADD CONSTRAINT ck_consumer_consent_actions_action "
        "CHECK (action IN ('grant','lead_capture','withdraw','wechat_bind'))"
    )
    op.execute(_LEAD_V2_SQL)
    op.execute(_OAUTH_BIND_SQL)
    for signature in (_LEAD_V2, _OAUTH_BIND):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _role_exists("yimatong_app"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_LEAD_V2} TO yimatong_app")
    if _role_exists("yimatong_callback"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_OAUTH_BIND} TO yimatong_callback")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM public.consumer_consent_actions WHERE action='wechat_bind' LIMIT 1")
    ).first():
        raise RuntimeError("cannot downgrade consumer OAuth authority while immutable binding audits exist")
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM public.consumer_profiles WHERE phone_hash IS NOT NULL LIMIT 1")
    ).first():
        _restore_legacy_phones()
    op.execute(f"DROP FUNCTION IF EXISTS public.{_OAUTH_BIND}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_LEAD_V2}")
    op.execute("ALTER TABLE public.consumer_consent_actions DROP CONSTRAINT ck_consumer_consent_actions_action")
    op.execute(
        "ALTER TABLE public.consumer_consent_actions ADD CONSTRAINT ck_consumer_consent_actions_action "
        "CHECK (action IN ('grant','lead_capture','withdraw'))"
    )
    op.drop_constraint(
        "fk_consumer_consent_policy_current_policy_purpose",
        "consumer_consent_policy_current",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_consumer_consent_policies_tenant_purpose_id", "consumer_consent_policies", type_="unique"
    )
    op.drop_constraint("ck_consumer_profiles_phone_envelope_format", "consumer_profiles", type_="check")
    op.drop_constraint("ck_consumer_profiles_phone_envelope", "consumer_profiles", type_="check")
    op.drop_column("consumer_profiles", "phone_key_id")
    op.drop_column("consumer_profiles", "phone_nonce")
    op.drop_column("consumer_profiles", "phone_ciphertext")
    op.drop_index("uq_consumer_phone_keys_single_active", table_name="consumer_phone_encryption_keys")
    op.drop_table("consumer_phone_encryption_keys")
