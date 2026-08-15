"""cut over consumer phone envelopes

Revision ID: u6f2c3d4e5f6
Revises: u6f1b2c3d4e5
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6f2c3d4e5f6"
down_revision: str | None = "u6f1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEAD_V1 = "capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text)"
_LEAD_V1_PRIVATE = (
    "capture_consumer_lead_legacy(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text)"
)
_LEAD_V2 = (
    "capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text)"
)
_CREATE_ANONYMOUS = "create_anonymous_consumer_profile(uuid,uuid,uuid,uuid)"


def _crypto_ready() -> None:
    from app.utils.crypto import EnvKeyProvider, init_crypto

    init_crypto(EnvKeyProvider())


def _configured_active_key_id() -> str:
    from app.utils.crypto import encrypt_bytes

    _ciphertext, _nonce, key_id = encrypt_bytes(b"", aad=b"consumer-phone-key-registry-v1")
    return key_id


def _verify_envelopes() -> None:
    from app.utils.crypto import decrypt_consumer_phone, hash_phone

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id "
                "FROM public.consumer_profiles WHERE phone_hash IS NOT NULL ORDER BY tenant_id,id"
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
            raise RuntimeError(f"consumer phone v2 cutover verification failed for consumer_id={consumer_id}")


def _restore_legacy_envelopes() -> None:
    from app.utils.crypto import decrypt_consumer_phone, encrypt_phone, hash_phone

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
            raise RuntimeError(f"consumer phone rollback verification failed for consumer_id={consumer_id}")
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
     OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)='' THEN
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
  payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||requested_phone_hash||':'||
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
  SELECT profile.* INTO profile_row FROM public.consumer_profiles AS profile
   WHERE profile.tenant_id=requested_tenant_id AND profile.phone_hash=requested_phone_hash FOR UPDATE NOWAIT;
  IF FOUND THEN
    IF requested_token_consumer_id IS NULL OR profile_row.id IS DISTINCT FROM requested_token_consumer_id THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='lead consumer subject denied';
    END IF;
  ELSE
    INSERT INTO public.consumer_profiles(id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,
        nickname,member_level,total_points,extra_data,lead_contact_suppressed,lead_consent_id,
        lead_scan_event_id,lead_scan_event_time,lead_captured_at)
    VALUES(requested_consumer_id,requested_tenant_id,requested_phone_hash,requested_phone_ciphertext,
        requested_phone_nonce,requested_phone_key_id,requested_name,'normal',0,coalesce(requested_lead_extra,'{}'),
        false,requested_consent_id,requested_scan_event_id,requested_scan_time,statement_timestamp())
    RETURNING * INTO profile_row;made:=true;
  END IF;
  IF NOT made THEN
    UPDATE public.consumer_profiles AS profile SET phone_ciphertext=requested_phone_ciphertext,
      phone_nonce=requested_phone_nonce,phone_key_id=requested_phone_key_id,
      nickname=coalesce(requested_name,profile.nickname),
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
END $f$;
"""

_WITHDRAW_V2_SQL = r"""
CREATE OR REPLACE FUNCTION public.withdraw_consumer_consent(
  requested_tenant_id uuid,requested_consent_id uuid,requested_audit_id uuid,requested_scan_event_id uuid,
  requested_scan_time timestamptz,requested_public_id text,requested_visitor_id text,
  requested_token_consumer_id uuid,requested_idempotency_key text
) RETURNS TABLE(consent_id uuid,status text,consumer_id uuid,contact_suppressed boolean,
                withdrawn_at timestamptz,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE consent_row record;existing_action record;subject_hash text;payload_hash text;suppressed boolean:=false;
BEGIN
  IF requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)='' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid withdrawal';
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
  IF consent_row.authority_version<>1 OR consent_row.visitor_subject_hash<>subject_hash
     OR consent_row.public_id<>requested_public_id
     OR (consent_row.consumer_id IS NOT NULL
         AND consent_row.consumer_id IS DISTINCT FROM requested_token_consumer_id) THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='withdrawal subject denied';
  END IF;
  payload_hash:=encode(digest(convert_to(requested_consent_id::text||':'||subject_hash,'UTF8'),'sha256'),'hex');
  SELECT action_row.* INTO existing_action FROM public.consumer_consent_actions AS action_row
   WHERE action_row.tenant_id=requested_tenant_id AND action_row.action='withdraw'
     AND action_row.idempotency_key=requested_idempotency_key;
  IF FOUND THEN
    IF existing_action.payload_hash IS DISTINCT FROM payload_hash THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='withdraw idempotency conflict';
    END IF;
    SELECT profile.lead_contact_suppressed INTO suppressed FROM public.consumer_profiles AS profile
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id;
    consent_id:=consent_row.id;status:=consent_row.status;consumer_id:=consent_row.consumer_id;
    contact_suppressed:=coalesce(suppressed,false);withdrawn_at:=consent_row.withdrawn_at;
    replayed:=true;RETURN NEXT;RETURN;
  END IF;
  IF consent_row.status='withdrawn' THEN
    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='consent already withdrawn';
  END IF;
  UPDATE public.consent_records AS record SET status='withdrawn',withdrawn_at=statement_timestamp(),
      updated_at=statement_timestamp()
   WHERE record.tenant_id=requested_tenant_id AND record.id=requested_consent_id RETURNING * INTO consent_row;
  IF consent_row.purpose='lead_capture' AND consent_row.consumer_id IS NOT NULL THEN
    UPDATE public.consumer_profiles AS profile SET lead_contact_suppressed=true,phone_hash=NULL,
        phone_ciphertext=NULL,phone_nonce=NULL,phone_key_id=NULL,nickname=NULL,
        extra_data=(coalesce(profile.extra_data,'{}')::jsonb-'region'-'intention')::json,
        updated_at=statement_timestamp()
     WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id
       AND profile.lead_consent_id=consent_row.id
     RETURNING profile.lead_contact_suppressed INTO suppressed;
    IF NOT FOUND THEN
      SELECT profile.lead_contact_suppressed INTO suppressed FROM public.consumer_profiles AS profile
       WHERE profile.tenant_id=requested_tenant_id AND profile.id=consent_row.consumer_id;
    END IF;
  END IF;
  INSERT INTO public.consumer_consent_actions(id,tenant_id,consent_id,policy_id,consumer_id,action,
      idempotency_key,payload_hash,visitor_subject_hash,result_status)
  VALUES(requested_audit_id,requested_tenant_id,requested_consent_id,consent_row.policy_id,
      consent_row.consumer_id,'withdraw',requested_idempotency_key,payload_hash,subject_hash,'withdrawn');
  consent_id:=consent_row.id;status:='withdrawn';consumer_id:=consent_row.consumer_id;
  contact_suppressed:=suppressed;withdrawn_at:=consent_row.withdrawn_at;replayed:=false;RETURN NEXT;
END $f$;
"""

_WITHDRAW_V1_SQL = _WITHDRAW_V2_SQL.replace(
    "phone_ciphertext=NULL,phone_nonce=NULL,phone_key_id=NULL", "phone_encrypted=NULL"
)

_GUARD_V2_SQL = r"""
CREATE OR REPLACE FUNCTION public.guard_consumer_lead_authority_fields() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
BEGIN
  IF current_user='yimatong_app' THEN
    IF TG_OP='INSERT' AND (NEW.phone_hash IS NOT NULL OR NEW.phone_ciphertext IS NOT NULL
       OR NEW.phone_nonce IS NOT NULL OR NEW.phone_key_id IS NOT NULL OR NEW.nickname IS NOT NULL
       OR coalesce(NEW.extra_data,'{}')::jsonb<>'{}'::jsonb OR NEW.lead_contact_suppressed IS TRUE
       OR NEW.lead_consent_id IS NOT NULL OR NEW.lead_scan_event_id IS NOT NULL
       OR NEW.lead_scan_event_time IS NOT NULL OR NEW.lead_captured_at IS NOT NULL) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer lead fields require authority function';
    ELSIF TG_OP='UPDATE' AND (OLD.phone_hash IS DISTINCT FROM NEW.phone_hash
       OR OLD.phone_ciphertext IS DISTINCT FROM NEW.phone_ciphertext
       OR OLD.phone_nonce IS DISTINCT FROM NEW.phone_nonce OR OLD.phone_key_id IS DISTINCT FROM NEW.phone_key_id
       OR OLD.nickname IS DISTINCT FROM NEW.nickname OR OLD.extra_data::jsonb IS DISTINCT FROM NEW.extra_data::jsonb
       OR OLD.lead_contact_suppressed IS DISTINCT FROM NEW.lead_contact_suppressed
       OR OLD.lead_consent_id IS DISTINCT FROM NEW.lead_consent_id
       OR OLD.lead_scan_event_id IS DISTINCT FROM NEW.lead_scan_event_id
       OR OLD.lead_scan_event_time IS DISTINCT FROM NEW.lead_scan_event_time
       OR OLD.lead_captured_at IS DISTINCT FROM NEW.lead_captured_at) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer lead fields require authority function';
    END IF;
  END IF;
  RETURN NEW;
END $f$;
"""

_GUARD_V1_SQL = r"""
CREATE OR REPLACE FUNCTION public.guard_consumer_lead_authority_fields() RETURNS trigger
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
       OR OLD.lead_consent_id IS DISTINCT FROM NEW.lead_consent_id
       OR OLD.lead_scan_event_id IS DISTINCT FROM NEW.lead_scan_event_id
       OR OLD.lead_scan_event_time IS DISTINCT FROM NEW.lead_scan_event_time
       OR OLD.lead_captured_at IS DISTINCT FROM NEW.lead_captured_at) THEN
      RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer lead fields require authority function';
    END IF;
  END IF;
  RETURN NEW;
END $f$;
"""

_CREATE_ANONYMOUS_SQL = r"""
CREATE FUNCTION public.create_anonymous_consumer_profile(
  requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
  requested_consumer_id uuid
) RETURNS TABLE(consumer_id uuid,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE actor_id uuid;principal_tenant_id uuid;now_at timestamptz:=statement_timestamp();
BEGIN
  IF requested_auth_session_id IS NULL OR requested_audit_id IS NULL OR requested_consumer_id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='anonymous consumer request is invalid';
  END IF;
  IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='anonymous consumer tenant denied';
  END IF;
  PERFORM pg_advisory_xact_lock_shared(6434892150882653249);
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_auth_session_id::text,0));
  SELECT session.tenant_id,account.id INTO principal_tenant_id,actor_id
    FROM public.auth_sessions AS session
    JOIN public.accounts AS account ON account.tenant_id=session.tenant_id AND account.id=session.account_id
    JOIN public.tenants AS tenant ON tenant.id=session.tenant_id
   WHERE session.id=requested_auth_session_id AND session.revoked_at IS NULL AND session.expires_at>now_at
     AND session.auth_version=account.auth_version AND account.is_active AND tenant.status='active'
   FOR SHARE OF session,account,tenant;
  IF actor_id IS NULL OR principal_tenant_id IS DISTINCT FROM requested_tenant_id THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='anonymous consumer actor denied';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM public.account_roles AS account_role
    JOIN public.roles AS role ON role.tenant_id=account_role.tenant_id AND role.id=account_role.role_id
    JOIN public.role_permissions AS role_permission ON role_permission.tenant_id=account_role.tenant_id
      AND role_permission.role_id=account_role.role_id
    JOIN public.permissions AS permission ON permission.tenant_id=role_permission.tenant_id
      AND permission.id=role_permission.permission_id
    WHERE account_role.tenant_id=requested_tenant_id AND account_role.account_id=actor_id
      AND role.name IN ('admin','operator') AND permission.code='consumer:detail'
  ) THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='anonymous consumer permission denied';
  END IF;
  INSERT INTO public.consumer_profiles(id,tenant_id,member_level,total_points,extra_data,
      lead_contact_suppressed,created_at,updated_at)
  VALUES(requested_consumer_id,requested_tenant_id,'normal',0,'{}'::json,false,now_at,now_at);
  INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,
      timestamp,created_at,updated_at)
  VALUES(requested_audit_id,actor_id::text,requested_tenant_id::text,'consumer_profile_created',
      'consumer_profile:'||requested_consumer_id::text,
      jsonb_build_object('contact_fields','empty','result','success'),now_at,now_at,now_at);
  consumer_id:=requested_consumer_id;created_at:=now_at;RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='anonymous consumer authority is busy';
END $f$;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _crypto_ready()
    active_key = op.get_bind().execute(
        sa.text("SELECT key_id FROM consumer_phone_encryption_keys WHERE status='active'")
    ).scalar_one_or_none()
    if active_key != _configured_active_key_id():
        raise RuntimeError("consumer phone cutover active key does not match the configured provider")
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("LOCK TABLE public.consumer_profiles IN SHARE ROW EXCLUSIVE MODE")
    if op.get_bind().execute(
        sa.text(
            "SELECT id FROM consumer_profiles WHERE phone_hash IS NOT NULL AND "
            "(phone_ciphertext IS NULL OR phone_nonce IS NULL OR phone_key_id IS NULL) LIMIT 1"
        )
    ).first():
        raise RuntimeError("consumer phone cutover requires a complete v2 backfill")
    _verify_envelopes()
    op.execute("ALTER TABLE public.consumer_profiles VALIDATE CONSTRAINT ck_consumer_profiles_phone_envelope")
    op.execute(
        "ALTER TABLE public.consumer_profiles VALIDATE CONSTRAINT ck_consumer_profiles_phone_envelope_format"
    )
    op.execute(
        "ALTER TABLE public.consumer_consent_policy_current VALIDATE CONSTRAINT "
        "fk_consumer_consent_policy_current_policy_purpose"
    )
    op.drop_constraint(
        "fk_consumer_consent_policy_current_policy",
        "consumer_consent_policy_current",
        type_="foreignkey",
    )
    op.execute(
        "ALTER TABLE public.consumer_consent_policy_current RENAME CONSTRAINT "
        "fk_consumer_consent_policy_current_policy_purpose TO fk_consumer_consent_policy_current_policy"
    )
    op.execute(f"DROP FUNCTION public.{_LEAD_V2}")
    op.execute(_LEAD_V2_SQL)
    op.execute(_WITHDRAW_V2_SQL)
    op.execute(_GUARD_V2_SQL)
    op.execute(_CREATE_ANONYMOUS_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CREATE_ANONYMOUS} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_LEAD_V2} FROM PUBLIC")
    if bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"))
        .scalar_one()
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_LEAD_V2} TO yimatong_app")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_CREATE_ANONYMOUS} TO yimatong_app")
        op.execute(f"REVOKE ALL ON FUNCTION public.{_LEAD_V1} FROM yimatong_app")
        op.execute("REVOKE INSERT ON TABLE public.consumer_profiles FROM yimatong_app")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_LEAD_V1} FROM PUBLIC")
    op.execute(
        "ALTER FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,"
        "text,text,text,jsonb,text) RENAME TO capture_consumer_lead_legacy"
    )
    op.drop_column("consumer_profiles", "phone_encrypted")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{_CREATE_ANONYMOUS}")
    op.add_column("consumer_profiles", sa.Column("phone_encrypted", sa.Text(), nullable=True))
    _crypto_ready()
    _restore_legacy_envelopes()
    op.execute(_WITHDRAW_V1_SQL)
    op.execute(_GUARD_V1_SQL)
    op.execute(
        "ALTER FUNCTION public.capture_consumer_lead_legacy(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,"
        "text,text,text,jsonb,text) RENAME TO capture_consumer_lead"
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_LEAD_V1} FROM PUBLIC")
    if bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"))
        .scalar_one()
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_LEAD_V1} TO yimatong_app")
        op.execute("GRANT INSERT ON TABLE public.consumer_profiles TO yimatong_app")
    op.execute(
        "ALTER TABLE public.consumer_consent_policy_current RENAME CONSTRAINT "
        "fk_consumer_consent_policy_current_policy TO fk_consumer_consent_policy_current_policy_purpose"
    )
    op.create_foreign_key(
        "fk_consumer_consent_policy_current_policy",
        "consumer_consent_policy_current",
        "consumer_consent_policies",
        ["tenant_id", "policy_id"],
        ["tenant_id", "id"],
    )
