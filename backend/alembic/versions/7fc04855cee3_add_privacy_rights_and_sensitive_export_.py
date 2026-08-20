"""Add privacy rights and sensitive export authority.

Revision ID: 7fc04855cee3
Revises: 26336e5a1635
"""

from typing import Sequence, Union

from alembic import op

revision: str = "7fc04855cee3"
down_revision: Union[str, None] = "26336e5a1635"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "privacy_rights_requests",
    "privacy_rights_events",
    "member_pii_access_events",
    "sensitive_member_exports",
    "sensitive_member_export_events",
)


def _plain(sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _plain(
        r"""
        CREATE TABLE public.privacy_rights_requests(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,request_number varchar(40) NOT NULL,
          consumer_id uuid NOT NULL,membership_id uuid,request_type varchar(30) NOT NULL,
          status varchar(30) NOT NULL DEFAULT 'submitted',owner_account_id uuid,due_at timestamptz NOT NULL,
          verified_at timestamptz,restricted_at timestamptz,completed_at timestamptz,outcome varchar(1000),
          evidence jsonb NOT NULL DEFAULT '{}'::jsonb,created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_privacy_rights_requests_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_privacy_rights_requests_number UNIQUE(tenant_id,request_number),
          CONSTRAINT fk_privacy_rights_requests_tenant FOREIGN KEY(tenant_id) REFERENCES public.tenants(id),
          CONSTRAINT fk_privacy_rights_requests_consumer FOREIGN KEY(tenant_id,consumer_id) REFERENCES public.consumer_profiles(tenant_id,id),
          CONSTRAINT fk_privacy_rights_requests_membership FOREIGN KEY(tenant_id,membership_id) REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT fk_privacy_rights_requests_owner FOREIGN KEY(tenant_id,owner_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_privacy_rights_request_type CHECK(request_type IN ('access','copy','correct','delete','restrict','withdraw_consent','close_membership')),
          CONSTRAINT ck_privacy_rights_status CHECK(status IN ('submitted','verified','assigned','restricted','completed','rejected')),
          CONSTRAINT ck_privacy_rights_completion CHECK((status IN ('completed','rejected') AND completed_at IS NOT NULL AND outcome IS NOT NULL) OR status NOT IN ('completed','rejected'))
        );
        CREATE INDEX ix_privacy_rights_requests_queue ON public.privacy_rights_requests(tenant_id,status,due_at);
        CREATE TABLE public.privacy_rights_events(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,request_id uuid NOT NULL,action varchar(40) NOT NULL,
          from_status varchar(30),to_status varchar(30),reason varchar(1000) NOT NULL,evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
          actor_kind varchar(20) NOT NULL,actor_ref varchar(100) NOT NULL,previous_digest varchar(64) NOT NULL,
          event_digest varchar(64) NOT NULL,occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_privacy_rights_events_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT fk_privacy_rights_events_request FOREIGN KEY(tenant_id,request_id) REFERENCES public.privacy_rights_requests(tenant_id,id),
          CONSTRAINT ck_privacy_rights_event_actor CHECK(actor_kind IN ('consumer','account','system')),
          CONSTRAINT ck_privacy_rights_event_digest CHECK(previous_digest ~ '^[0-9a-f]{64}$' AND event_digest ~ '^[0-9a-f]{64}$')
        );
        CREATE INDEX ix_privacy_rights_events_request ON public.privacy_rights_events(tenant_id,request_id,occurred_at);
        CREATE TABLE public.member_pii_access_events(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,consumer_id uuid NOT NULL,actor_account_id uuid NOT NULL,
          fields jsonb NOT NULL,outcome varchar(20) NOT NULL,reason varchar(1000) NOT NULL,ticket_ref varchar(160),
          request_trace_id varchar(100),event_digest varchar(64) NOT NULL,occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_member_pii_access_events_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT fk_member_pii_access_events_consumer FOREIGN KEY(tenant_id,consumer_id) REFERENCES public.consumer_profiles(tenant_id,id),
          CONSTRAINT fk_member_pii_access_events_actor FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_member_pii_access_outcome CHECK(outcome IN ('authorized','succeeded','failed','denied')),
          CONSTRAINT ck_member_pii_access_digest CHECK(event_digest ~ '^[0-9a-f]{64}$')
        );
        CREATE INDEX ix_member_pii_access_events_subject ON public.member_pii_access_events(tenant_id,consumer_id,occurred_at);
        CREATE TABLE public.sensitive_member_exports(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,requester_account_id uuid NOT NULL,approver_account_id uuid,
          status varchar(30) NOT NULL DEFAULT 'pending_approval',reason varchar(1000) NOT NULL,
          recipient_purpose varchar(500) NOT NULL,requested_fields jsonb NOT NULL,filters jsonb NOT NULL DEFAULT '{}'::jsonb,
          includes_full_pii boolean NOT NULL DEFAULT true,row_count integer,artifact_ciphertext bytea,artifact_nonce bytea,
          artifact_key_id varchar(64),artifact_size_bytes bigint,checksum_sha256 varchar(64),download_token_digest varchar(64),
          expires_at timestamptz,downloaded_at timestamptz,deleted_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_sensitive_member_exports_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT fk_sensitive_member_exports_tenant FOREIGN KEY(tenant_id) REFERENCES public.tenants(id),
          CONSTRAINT fk_sensitive_member_exports_requester FOREIGN KEY(tenant_id,requester_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT fk_sensitive_member_exports_approver FOREIGN KEY(tenant_id,approver_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_sensitive_member_exports_status CHECK(status IN ('pending_approval','approved','prepared','downloaded','rejected','deleted')),
          CONSTRAINT ck_sensitive_member_exports_separation CHECK(approver_account_id IS NULL OR approver_account_id<>requester_account_id),
          CONSTRAINT ck_sensitive_member_exports_artifact CHECK((status NOT IN ('prepared','downloaded') AND artifact_ciphertext IS NULL) OR
            (status IN ('prepared','downloaded') AND artifact_ciphertext IS NOT NULL AND octet_length(artifact_nonce)=12
             AND artifact_key_id ~ '^[A-Za-z0-9._:-]{1,64}$' AND checksum_sha256 ~ '^[0-9a-f]{64}$'
             AND expires_at IS NOT NULL AND ((status='prepared' AND download_token_digest ~ '^[0-9a-f]{64}$')
               OR (status='downloaded' AND download_token_digest IS NULL))))
        );
        CREATE INDEX ix_sensitive_member_exports_queue ON public.sensitive_member_exports(tenant_id,status,expires_at);
        CREATE TABLE public.sensitive_member_export_events(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,export_id uuid NOT NULL,action varchar(30) NOT NULL,
          actor_account_id uuid NOT NULL,outcome varchar(20) NOT NULL,reason varchar(1000) NOT NULL,
          metadata_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,previous_digest varchar(64) NOT NULL,event_digest varchar(64) NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_sensitive_member_export_events_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT fk_sensitive_member_export_events_export FOREIGN KEY(tenant_id,export_id) REFERENCES public.sensitive_member_exports(tenant_id,id),
          CONSTRAINT fk_sensitive_member_export_events_actor FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_sensitive_member_export_event_digest CHECK(previous_digest ~ '^[0-9a-f]{64}$' AND event_digest ~ '^[0-9a-f]{64}$')
        );
        CREATE INDEX ix_sensitive_member_export_events_export ON public.sensitive_member_export_events(tenant_id,export_id,occurred_at);
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON public.{table} TO yimatong_app USING(tenant_id=public.current_tenant_id())")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,yimatong_app,yimatong_callback")
        if table != "sensitive_member_exports":
            op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")

    op.execute(
        "CREATE VIEW public.sensitive_member_export_summaries WITH (security_invoker=true) AS SELECT "
        "id,tenant_id,requester_account_id,approver_account_id,status,reason,recipient_purpose,requested_fields,filters,"
        "includes_full_pii,row_count,artifact_size_bytes,checksum_sha256,expires_at,downloaded_at,deleted_at,created_at,updated_at "
        "FROM public.sensitive_member_exports"
    )
    op.execute("GRANT SELECT ON public.sensitive_member_export_summaries TO yimatong_app")
    op.execute(
        "GRANT SELECT (id,tenant_id,requester_account_id,approver_account_id,status,reason,recipient_purpose,"
        "requested_fields,filters,includes_full_pii,row_count,artifact_size_bytes,checksum_sha256,expires_at,"
        "downloaded_at,deleted_at,created_at,updated_at) ON public.sensitive_member_exports TO yimatong_app"
    )

    op.execute(
        r"""CREATE FUNCTION public.privacy_actor_authority(requested_tenant_id uuid,requested_session_id uuid,
          requested_actor uuid,required_permission text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $f$ DECLARE resolved_actor uuid; BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='privacy authority denied'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_session_id::text,0));
          SELECT account.id INTO resolved_actor FROM public.auth_sessions auth_session
          JOIN public.accounts account ON account.tenant_id=auth_session.tenant_id AND account.id=auth_session.account_id
          JOIN public.tenants tenant ON tenant.id=auth_session.tenant_id
          WHERE auth_session.id=requested_session_id AND auth_session.tenant_id=requested_tenant_id
            AND auth_session.revoked_at IS NULL AND auth_session.expires_at>statement_timestamp()
            AND auth_session.auth_version=account.auth_version AND account.is_active AND tenant.status='active'
            AND EXISTS(SELECT 1 FROM public.account_roles account_role
              JOIN public.role_permissions role_permission ON role_permission.tenant_id=account_role.tenant_id AND role_permission.role_id=account_role.role_id
              JOIN public.permissions permission ON permission.tenant_id=role_permission.tenant_id AND permission.id=role_permission.permission_id
              WHERE account_role.tenant_id=requested_tenant_id AND account_role.account_id=account.id AND permission.code=required_permission);
          IF resolved_actor IS NULL OR resolved_actor IS DISTINCT FROM requested_actor THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='privacy session or permission denied'; END IF;
          RETURN resolved_actor; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.privacy_actor_authority(uuid,uuid,uuid,text) FROM PUBLIC,yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.append_privacy_rights_event(requested_tenant_id uuid,requested_event_id uuid,
          requested_request_id uuid,requested_action text,requested_from text,requested_to text,requested_reason text,
          requested_evidence jsonb,requested_actor_kind text,requested_actor_ref text) RETURNS void
          LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
          DECLARE prior text;calculated text; BEGIN
          SELECT event_digest INTO prior FROM public.privacy_rights_events WHERE tenant_id=requested_tenant_id
            AND request_id=requested_request_id ORDER BY occurred_at DESC,id DESC LIMIT 1 FOR SHARE;
          prior:=COALESCE(prior,repeat('0',64));
          calculated:=encode(digest(convert_to(jsonb_build_array(prior,requested_tenant_id,requested_event_id,
            requested_request_id,requested_action,requested_from,requested_to,requested_reason,requested_evidence,
            requested_actor_kind,requested_actor_ref)::text,'UTF8'),'sha256'),'hex');
          INSERT INTO public.privacy_rights_events(id,tenant_id,request_id,action,from_status,to_status,reason,evidence,
            actor_kind,actor_ref,previous_digest,event_digest) VALUES(requested_event_id,requested_tenant_id,
            requested_request_id,requested_action,requested_from,requested_to,requested_reason,COALESCE(requested_evidence,'{}'),
            requested_actor_kind,requested_actor_ref,prior,calculated); END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.append_privacy_rights_event(uuid,uuid,uuid,text,text,text,text,jsonb,text,text) FROM PUBLIC,yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.create_consumer_privacy_request_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE target_id uuid:=(payload->>'request_id')::uuid;target_consumer uuid:=(payload->>'consumer_id')::uuid;
          target_membership uuid:=(payload->>'membership_id')::uuid;existing_id uuid; BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR NOT EXISTS(SELECT 1 FROM public.brand_membership_profile_links link
               JOIN public.brand_memberships membership ON membership.tenant_id=link.tenant_id AND membership.id=link.membership_id
               WHERE link.tenant_id=requested_tenant_id AND link.membership_id=target_membership
                 AND link.consumer_profile_id=target_consumer AND membership.status='active') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='consumer privacy request authority denied'; END IF;
          SELECT id INTO existing_id FROM public.privacy_rights_requests WHERE tenant_id=requested_tenant_id
            AND consumer_id=target_consumer AND status NOT IN ('completed','rejected') AND request_type=payload->>'request_type';
          IF existing_id IS NOT NULL THEN RETURN existing_id; END IF;
          INSERT INTO public.privacy_rights_requests(id,tenant_id,request_number,consumer_id,membership_id,request_type,
            due_at,evidence) VALUES(target_id,requested_tenant_id,payload->>'request_number',target_consumer,target_membership,
            payload->>'request_type',statement_timestamp()+interval '15 days',COALESCE(payload->'evidence','{}'));
          PERFORM public.append_privacy_rights_event(requested_tenant_id,(payload->>'event_id')::uuid,target_id,'submitted',
            NULL,'submitted',payload->>'reason',payload->'evidence','consumer',target_membership::text); RETURN target_id; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.create_consumer_privacy_request_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.create_consumer_privacy_request_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.mutate_privacy_rights_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE actor uuid;item public.privacy_rights_requests%ROWTYPE;action_name text:=payload->>'action';next_status text;
          target_id uuid:=(payload->>'request_id')::uuid; BEGIN
          actor:=public.privacy_actor_authority(requested_tenant_id,(payload->>'auth_session_id')::uuid,
            (payload->>'actor_account_id')::uuid,'privacy:manage');
          SELECT * INTO item FROM public.privacy_rights_requests WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='privacy request not found'; END IF;
          IF action_name='assign' THEN next_status:='assigned';
            UPDATE public.privacy_rights_requests SET owner_account_id=(payload->>'owner_account_id')::uuid,status=next_status,
              verified_at=COALESCE(verified_at,statement_timestamp()),updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=target_id;
          ELSIF action_name='restrict' THEN next_status:='restricted';
            UPDATE public.privacy_rights_requests SET status=next_status,restricted_at=statement_timestamp(),
              evidence=evidence||COALESCE(payload->'evidence','{}'),updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=target_id;
            UPDATE public.consumer_profiles SET lead_contact_suppressed=true,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND id=item.consumer_id;
          ELSIF action_name IN ('complete','reject') THEN next_status:=CASE action_name WHEN 'complete' THEN 'completed' ELSE 'rejected' END;
            IF NULLIF(btrim(payload->>'outcome'),'') IS NULL THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='privacy outcome required'; END IF;
            UPDATE public.privacy_rights_requests SET status=next_status,outcome=payload->>'outcome',completed_at=statement_timestamp(),
              evidence=evidence||COALESCE(payload->'evidence','{}'),updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=target_id;
            IF item.request_type='delete' AND action_name='complete' THEN
              UPDATE public.consumer_profiles SET nickname=NULL,phone_hash=NULL,phone_ciphertext=NULL,phone_nonce=NULL,
                phone_key_id=NULL,wechat_openid_hash=NULL,wechat_openid_ciphertext=NULL,wechat_openid_nonce=NULL,
                wechat_openid_key_id=NULL,lead_contact_suppressed=true,updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=item.consumer_id; END IF;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid privacy request action'; END IF;
          PERFORM public.append_privacy_rights_event(requested_tenant_id,(payload->>'event_id')::uuid,target_id,action_name,
            item.status,next_status,payload->>'reason',payload->'evidence','account',actor::text); RETURN target_id; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_privacy_rights_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_privacy_rights_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.record_member_pii_access_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE actor uuid;target_id uuid:=(payload->>'event_id')::uuid;calculated text; BEGIN
          actor:=public.privacy_actor_authority(requested_tenant_id,(payload->>'auth_session_id')::uuid,
            (payload->>'actor_account_id')::uuid,'consumer:pii_reveal');
          IF NOT EXISTS(SELECT 1 FROM public.consumer_profiles WHERE tenant_id=requested_tenant_id AND id=(payload->>'consumer_id')::uuid)
             OR jsonb_typeof(payload->'fields')<>'array' OR NULLIF(btrim(payload->>'reason'),'') IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid pii access event'; END IF;
          calculated:=encode(digest(convert_to(jsonb_build_array(requested_tenant_id,target_id,payload->>'consumer_id',actor,
            payload->'fields',payload->>'outcome',payload->>'reason',payload->>'ticket_ref',payload->>'request_trace_id')::text,'UTF8'),'sha256'),'hex');
          INSERT INTO public.member_pii_access_events(id,tenant_id,consumer_id,actor_account_id,fields,outcome,reason,
            ticket_ref,request_trace_id,event_digest) VALUES(target_id,requested_tenant_id,(payload->>'consumer_id')::uuid,
            actor,payload->'fields',payload->>'outcome',payload->>'reason',NULLIF(payload->>'ticket_ref',''),
            NULLIF(payload->>'request_trace_id',''),calculated); RETURN target_id; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.record_member_pii_access_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.record_member_pii_access_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.append_sensitive_export_event(requested_tenant_id uuid,requested_event_id uuid,
          requested_export_id uuid,requested_action text,requested_actor uuid,requested_outcome text,requested_reason text,
          requested_metadata jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE prior text;calculated text; BEGIN SELECT event_digest INTO prior FROM public.sensitive_member_export_events
          WHERE tenant_id=requested_tenant_id AND export_id=requested_export_id ORDER BY occurred_at DESC,id DESC LIMIT 1 FOR SHARE;
          prior:=COALESCE(prior,repeat('0',64));calculated:=encode(digest(convert_to(jsonb_build_array(prior,
            requested_tenant_id,requested_event_id,requested_export_id,requested_action,requested_actor,requested_outcome,
            requested_reason,requested_metadata)::text,'UTF8'),'sha256'),'hex');
          INSERT INTO public.sensitive_member_export_events(id,tenant_id,export_id,action,actor_account_id,outcome,reason,
            metadata_snapshot,previous_digest,event_digest) VALUES(requested_event_id,requested_tenant_id,requested_export_id,
            requested_action,requested_actor,requested_outcome,requested_reason,COALESCE(requested_metadata,'{}'),prior,calculated); END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.append_sensitive_export_event(uuid,uuid,uuid,text,uuid,text,text,jsonb) FROM PUBLIC,yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.mutate_sensitive_member_export_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS TABLE(export_id uuid,status text,artifact_ciphertext bytea,artifact_nonce bytea,artifact_key_id text,
          checksum_sha256 text,file_name text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE actor uuid;item public.sensitive_member_exports%ROWTYPE;action_name text:=payload->>'action';
          target_id uuid:=(payload->>'export_id')::uuid;required_permission text; BEGIN
          required_permission:=CASE WHEN action_name='request' THEN 'export:sensitive_request'
            WHEN action_name='approve' THEN 'export:sensitive_approve' ELSE 'export:sensitive_request' END;
          actor:=public.privacy_actor_authority(requested_tenant_id,(payload->>'auth_session_id')::uuid,
            (payload->>'actor_account_id')::uuid,required_permission);
          IF action_name='request' THEN
            INSERT INTO public.sensitive_member_exports(id,tenant_id,requester_account_id,status,reason,recipient_purpose,
              requested_fields,filters,includes_full_pii) VALUES(target_id,requested_tenant_id,actor,'pending_approval',
              payload->>'reason',payload->>'recipient_purpose',payload->'requested_fields',COALESCE(payload->'filters','{}'),true)
              RETURNING * INTO item;
          ELSE SELECT * INTO item FROM public.sensitive_member_exports WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='sensitive export not found'; END IF;
            IF action_name='approve' THEN
              IF item.status<>'pending_approval' OR actor=item.requester_account_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='independent sensitive export approval required'; END IF;
              UPDATE public.sensitive_member_exports SET status='approved',approver_account_id=actor,updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=target_id RETURNING * INTO item;
            ELSIF action_name='prepare' THEN
              IF item.status<>'approved' OR actor<>item.requester_account_id OR octet_length(decode(payload->>'artifact_ciphertext','base64'))<17
                OR octet_length(decode(payload->>'artifact_nonce','base64'))<>12 OR payload->>'checksum_sha256' !~ '^[0-9a-f]{64}$'
                OR payload->>'download_token_digest' !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='sensitive export preparation denied'; END IF;
              UPDATE public.sensitive_member_exports SET status='prepared',row_count=(payload->>'row_count')::integer,
                artifact_ciphertext=decode(payload->>'artifact_ciphertext','base64'),artifact_nonce=decode(payload->>'artifact_nonce','base64'),
                artifact_key_id=payload->>'artifact_key_id',artifact_size_bytes=(payload->>'artifact_size_bytes')::bigint,
                checksum_sha256=payload->>'checksum_sha256',download_token_digest=payload->>'download_token_digest',
                expires_at=statement_timestamp()+interval '24 hours',updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=target_id RETURNING * INTO item;
            ELSIF action_name='download' THEN
              IF item.status<>'prepared' OR actor<>item.requester_account_id OR item.expires_at<=statement_timestamp()
                 OR item.download_token_digest IS DISTINCT FROM payload->>'download_token_digest' THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='sensitive export download denied'; END IF;
              UPDATE public.sensitive_member_exports SET status='downloaded',downloaded_at=statement_timestamp(),
                download_token_digest=NULL,updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=target_id RETURNING * INTO item;
            ELSIF action_name='delete' THEN
              IF item.expires_at IS NULL OR item.expires_at>statement_timestamp() THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='sensitive export not expired'; END IF;
              UPDATE public.sensitive_member_exports SET status='deleted',artifact_ciphertext=NULL,artifact_nonce=NULL,
                artifact_key_id=NULL,download_token_digest=NULL,deleted_at=statement_timestamp(),updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=target_id RETURNING * INTO item;
            ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid sensitive export action'; END IF;
          END IF;
          PERFORM public.append_sensitive_export_event(requested_tenant_id,(payload->>'event_id')::uuid,target_id,
            action_name,actor,'success',payload->>'reason',payload-'artifact_ciphertext'-'artifact_nonce'-'download_token_digest');
          RETURN QUERY SELECT item.id,item.status::text,item.artifact_ciphertext,item.artifact_nonce,item.artifact_key_id::text,
            item.checksum_sha256::text,('member-sensitive-'||item.id::text||'.csv')::text; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_sensitive_member_export_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_sensitive_member_export_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.purge_expired_sensitive_exports_authority(requested_tenant_id uuid,requested_limit integer DEFAULT 100)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE item public.sensitive_member_exports%ROWTYPE;purged integer:=0;event_id uuid; BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR requested_limit<1 OR requested_limit>500 THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='sensitive export purge authority denied'; END IF;
          FOR item IN SELECT * FROM public.sensitive_member_exports WHERE tenant_id=requested_tenant_id
            AND status IN ('prepared','downloaded') AND expires_at<=statement_timestamp()
            ORDER BY expires_at,id FOR UPDATE SKIP LOCKED LIMIT requested_limit LOOP
            event_id:=gen_random_uuid();
            UPDATE public.sensitive_member_exports SET status='deleted',artifact_ciphertext=NULL,artifact_nonce=NULL,
              artifact_key_id=NULL,download_token_digest=NULL,deleted_at=statement_timestamp(),updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND id=item.id;
            PERFORM public.append_sensitive_export_event(requested_tenant_id,event_id,item.id,'expire_delete',
              item.requester_account_id,'success','24-hour retention policy',jsonb_build_object('initiator','system'));
            purged:=purged+1;
          END LOOP; RETURN purged; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_sensitive_exports_authority(uuid,integer) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.purge_expired_sensitive_exports_authority(uuid,integer) TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.reapply_completed_privacy_controls_authority(requested_tenant_id uuid)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE affected integer:=0;changed integer; BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='privacy recovery authority denied'; END IF;
          UPDATE public.consumer_profiles profile SET nickname=NULL,phone_hash=NULL,phone_ciphertext=NULL,phone_nonce=NULL,
            phone_key_id=NULL,wechat_openid_hash=NULL,wechat_openid_ciphertext=NULL,wechat_openid_nonce=NULL,
            wechat_openid_key_id=NULL,lead_contact_suppressed=true,updated_at=statement_timestamp()
          WHERE profile.tenant_id=requested_tenant_id AND EXISTS(
            SELECT 1 FROM public.privacy_rights_requests request WHERE request.tenant_id=profile.tenant_id
              AND request.consumer_id=profile.id AND request.request_type='delete' AND request.status='completed')
            AND (profile.nickname IS NOT NULL OR profile.phone_hash IS NOT NULL OR profile.phone_ciphertext IS NOT NULL
              OR profile.phone_nonce IS NOT NULL OR profile.phone_key_id IS NOT NULL OR profile.wechat_openid_hash IS NOT NULL
              OR profile.wechat_openid_ciphertext IS NOT NULL OR profile.wechat_openid_nonce IS NOT NULL
              OR profile.wechat_openid_key_id IS NOT NULL OR NOT profile.lead_contact_suppressed);
          GET DIAGNOSTICS changed=ROW_COUNT; affected:=affected+changed;
          UPDATE public.consumer_profiles profile SET lead_contact_suppressed=true,updated_at=statement_timestamp()
          WHERE profile.tenant_id=requested_tenant_id AND NOT profile.lead_contact_suppressed AND EXISTS(
            SELECT 1 FROM public.privacy_rights_requests request WHERE request.tenant_id=profile.tenant_id
              AND request.consumer_id=profile.id AND request.status='restricted');
          GET DIAGNOSTICS changed=ROW_COUNT; RETURN affected+changed; END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.reapply_completed_privacy_controls_authority(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.reapply_completed_privacy_controls_authority(uuid) TO yimatong_app")

    op.execute(
        r"""INSERT INTO public.permissions(id,tenant_id,code,description)
        SELECT gen_random_uuid(),tenant.id,permission.code,'会员隐私治理权限：'||permission.code FROM public.tenants tenant
        CROSS JOIN (VALUES('privacy:manage'),('export:sensitive_request'),('consumer:pii_reveal'),('export:sensitive_approve')) permission(code)
        WHERE NOT EXISTS(SELECT 1 FROM public.permissions existing
          WHERE existing.tenant_id=tenant.id AND existing.code=permission.code)"""
    )
    op.execute(
        r"""INSERT INTO public.role_permissions(tenant_id,role_id,permission_id)
        SELECT role.tenant_id,role.id,permission.id FROM public.roles role JOIN public.permissions permission
          ON permission.tenant_id=role.tenant_id AND permission.code IN ('privacy:manage','export:sensitive_request')
        WHERE role.name='admin' ON CONFLICT DO NOTHING"""
    )


def downgrade() -> None:
    op.execute(
        r"""DO $f$ BEGIN IF EXISTS(SELECT 1 FROM public.privacy_rights_events LIMIT 1)
        OR EXISTS(SELECT 1 FROM public.member_pii_access_events LIMIT 1)
        OR EXISTS(SELECT 1 FROM public.sensitive_member_export_events LIMIT 1)
        THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='privacy governance facts exist; archive before downgrade'; END IF; END $f$;"""
    )
    op.execute("DROP VIEW public.sensitive_member_export_summaries")
    for signature in (
        "reapply_completed_privacy_controls_authority(uuid)",
        "purge_expired_sensitive_exports_authority(uuid,integer)",
        "mutate_sensitive_member_export_authority(uuid,jsonb)",
        "append_sensitive_export_event(uuid,uuid,uuid,text,uuid,text,text,jsonb)",
        "record_member_pii_access_authority(uuid,jsonb)",
        "mutate_privacy_rights_authority(uuid,jsonb)",
        "create_consumer_privacy_request_authority(uuid,jsonb)",
        "append_privacy_rights_event(uuid,uuid,uuid,text,text,text,text,jsonb,text,text)",
        "privacy_actor_authority(uuid,uuid,uuid,text)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
