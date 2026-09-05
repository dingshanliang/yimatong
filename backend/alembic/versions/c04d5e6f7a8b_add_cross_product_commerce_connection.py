"""add cross-product commerce connection authority

Revision ID: c04d5e6f7a8b
Revises: 992308d73df7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c04d5e6f7a8b"
down_revision: str | Sequence[str] | None = "992308d73df7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "commerce_connections",
    "commerce_service_credentials",
    "commerce_member_references",
    "commerce_identity_handoffs",
    "commerce_integration_messages",
    "commerce_connection_events",
)


def _create_tables() -> None:
    statements = """
        CREATE TABLE public.commerce_connections(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES public.tenants(id),
          external_tenant_ref varchar(120) NOT NULL, external_shop_ref varchar(120) NOT NULL,
          base_url varchar(500) NOT NULL, capabilities jsonb NOT NULL, status varchar(20) NOT NULL,
          version bigint NOT NULL, created_by uuid NULL, disconnected_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_connections_tenant_creator FOREIGN KEY(tenant_id,created_by)
            REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT uq_commerce_connections_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT ck_commerce_connections_status CHECK(status IN ('active','disconnected')),
          CONSTRAINT ck_commerce_connections_version CHECK(version>0),
          CONSTRAINT ck_commerce_connections_lifecycle CHECK(
            (status='active' AND disconnected_at IS NULL)
            OR (status='disconnected' AND disconnected_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX uq_commerce_connections_active_tenant
          ON public.commerce_connections(tenant_id) WHERE status='active';

        CREATE TABLE public.commerce_service_credentials(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          direction varchar(30) NOT NULL, version integer NOT NULL, key_prefix varchar(24) NOT NULL,
          secret_ciphertext bytea NOT NULL, valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
          overlap_until timestamptz NULL, revoked_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_credentials_tenant_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT uq_commerce_credentials_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_credentials_prefix UNIQUE(key_prefix),
          CONSTRAINT uq_commerce_credentials_version UNIQUE(tenant_id,connection_id,direction,version),
          CONSTRAINT ck_commerce_credentials_direction CHECK(
            direction IN ('yimatong_to_commerce','commerce_to_yimatong')),
          CONSTRAINT ck_commerce_credentials_version CHECK(version>0),
          CONSTRAINT ck_commerce_credentials_validity CHECK(valid_until>valid_from),
          CONSTRAINT ck_commerce_credentials_overlap CHECK(
            overlap_until IS NULL OR (overlap_until>valid_from AND overlap_until<=valid_until))
        );
        CREATE INDEX ix_commerce_credentials_active
          ON public.commerce_service_credentials(tenant_id,connection_id,direction,valid_until);

        CREATE TABLE public.commerce_member_references(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          membership_id uuid NOT NULL, member_ref varchar(64) NOT NULL,
          external_customer_ref varchar(160) NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_member_refs_tenant_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT fk_commerce_member_refs_tenant_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT uq_commerce_member_refs_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_member_refs_member UNIQUE(tenant_id,connection_id,membership_id),
          CONSTRAINT uq_commerce_member_refs_ref UNIQUE(tenant_id,connection_id,member_ref)
        );
        CREATE UNIQUE INDEX uq_commerce_member_refs_external
          ON public.commerce_member_references(tenant_id,connection_id,external_customer_ref)
          WHERE external_customer_ref IS NOT NULL;
        CREATE INDEX ix_commerce_member_refs_membership
          ON public.commerce_member_references(tenant_id,membership_id);

        CREATE TABLE public.commerce_identity_handoffs(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          member_reference_id uuid NOT NULL, token_digest varchar(64) NOT NULL,
          expires_at timestamptz NOT NULL, redeemed_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_handoffs_tenant_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT fk_commerce_handoffs_tenant_member_ref FOREIGN KEY(tenant_id,member_reference_id)
            REFERENCES public.commerce_member_references(tenant_id,id),
          CONSTRAINT uq_commerce_handoffs_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_handoffs_digest UNIQUE(token_digest),
          CONSTRAINT ck_commerce_handoffs_validity CHECK(expires_at>created_at)
        );
        CREATE INDEX ix_commerce_handoffs_expiry
          ON public.commerce_identity_handoffs(tenant_id,connection_id,expires_at);
        CREATE INDEX ix_commerce_handoffs_member_ref
          ON public.commerce_identity_handoffs(tenant_id,member_reference_id);

        CREATE TABLE public.commerce_integration_messages(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          direction varchar(20) NOT NULL, message_id varchar(120) NOT NULL,
          message_version integer NOT NULL, message_type varchar(80) NOT NULL,
          credential_id uuid NULL, occurred_at timestamptz NOT NULL, payload jsonb NOT NULL,
          payload_digest varchar(64) NOT NULL, status varchar(20) NOT NULL,
          attempt_count integer NOT NULL DEFAULT 0, accepted_at timestamptz NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_messages_tenant_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT fk_commerce_messages_tenant_credential FOREIGN KEY(tenant_id,credential_id)
            REFERENCES public.commerce_service_credentials(tenant_id,id),
          CONSTRAINT uq_commerce_messages_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_messages_business_key UNIQUE(
            tenant_id,connection_id,direction,message_id,message_version),
          CONSTRAINT ck_commerce_messages_direction CHECK(direction IN ('inbox','outbox')),
          CONSTRAINT ck_commerce_messages_version CHECK(message_version>0),
          CONSTRAINT ck_commerce_messages_digest CHECK(length(payload_digest)=64),
          CONSTRAINT ck_commerce_messages_status CHECK(status IN ('accepted','pending','delivered','failed')),
          CONSTRAINT ck_commerce_messages_attempts CHECK(attempt_count>=0),
          CONSTRAINT ck_commerce_messages_direction_state CHECK(
            (direction='inbox' AND credential_id IS NOT NULL AND status='accepted' AND accepted_at IS NOT NULL)
            OR (direction='outbox' AND credential_id IS NULL AND accepted_at IS NULL))
        );
        CREATE INDEX ix_commerce_messages_reconcile
          ON public.commerce_integration_messages(tenant_id,connection_id,direction,status,created_at);
        CREATE INDEX ix_commerce_messages_credential
          ON public.commerce_integration_messages(tenant_id,credential_id);

        CREATE TABLE public.commerce_connection_events(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          event_type varchar(40) NOT NULL, idempotency_key varchar(120) NOT NULL,
          payload_digest varchar(64) NOT NULL, actor_id uuid NULL, details jsonb NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_events_tenant_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT uq_commerce_events_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_events_idempotency UNIQUE(tenant_id,idempotency_key),
          CONSTRAINT ck_commerce_events_type CHECK(event_type IN (
            'connected','credential_rotated','credential_revoked','disconnected',
            'handoff_issued','handoff_redeemed')),
          CONSTRAINT ck_commerce_events_digest CHECK(length(payload_digest)=64)
        );
        CREATE INDEX ix_commerce_events_connection_time
          ON public.commerce_connection_events(tenant_id,connection_id,occurred_at);
        """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())


def _create_rls_and_guards() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} "
            "USING (tenant_id=public.current_tenant_id()) "
            "WITH CHECK (tenant_id=public.current_tenant_id())"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
    op.execute(
        """
        CREATE FUNCTION public.guard_commerce_connection_event() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce connection events are immutable';
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_commerce_connection_event() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_commerce_connection_event BEFORE UPDATE OR DELETE "
        "ON public.commerce_connection_events FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_commerce_connection_event()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_commerce_authority_rows() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF TG_TABLE_NAME='commerce_connections' THEN
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
              OR NEW.external_tenant_ref IS DISTINCT FROM OLD.external_tenant_ref
              OR NEW.external_shop_ref IS DISTINCT FROM OLD.external_shop_ref
              OR NEW.base_url IS DISTINCT FROM OLD.base_url OR NEW.capabilities IS DISTINCT FROM OLD.capabilities
              OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce connection identity is immutable'; END IF;
          ELSIF TG_TABLE_NAME='commerce_service_credentials' THEN
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
              OR NEW.connection_id IS DISTINCT FROM OLD.connection_id OR NEW.direction IS DISTINCT FROM OLD.direction
              OR NEW.version IS DISTINCT FROM OLD.version OR NEW.key_prefix IS DISTINCT FROM OLD.key_prefix
              OR NEW.secret_ciphertext IS DISTINCT FROM OLD.secret_ciphertext
              OR NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
              OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce credential identity is immutable'; END IF;
          ELSIF TG_TABLE_NAME='commerce_member_references' THEN
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
              OR NEW.connection_id IS DISTINCT FROM OLD.connection_id
              OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
              OR NEW.member_ref IS DISTINCT FROM OLD.member_ref OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce member reference is immutable'; END IF;
          ELSIF TG_TABLE_NAME='commerce_identity_handoffs' THEN
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
              OR NEW.connection_id IS DISTINCT FROM OLD.connection_id
              OR NEW.member_reference_id IS DISTINCT FROM OLD.member_reference_id
              OR NEW.token_digest IS DISTINCT FROM OLD.token_digest OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
              OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce handoff identity is immutable'; END IF;
          END IF;
          RETURN NEW;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_commerce_authority_rows() FROM PUBLIC")
    for trigger_name, table in (
        ("trg_guard_commerce_connection", "commerce_connections"),
        ("trg_guard_commerce_credential", "commerce_service_credentials"),
        ("trg_guard_commerce_member_ref", "commerce_member_references"),
        ("trg_guard_commerce_handoff", "commerce_identity_handoffs"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_commerce_authority_rows()"
        )


def _create_authority_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_commerce_connection_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          action_name text := payload->>'action';
          target_connection_id uuid := (payload->>'connection_id')::uuid;
          event_name text;
          prior_event record;
          connection_row public.commerce_connections%ROWTYPE;
          credential_json jsonb;
          current_version integer;
          resolved_actor uuid;
          requested_auth_session_id uuid := NULLIF(payload->>'auth_session_id','')::uuid;
        BEGIN
          IF session_user<>'yimatong_app'
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR requested_auth_session_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce connection authority denied';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('auth-session:'||requested_auth_session_id::text,0));
          SELECT account.id INTO resolved_actor
          FROM public.auth_sessions auth_session
          JOIN public.accounts account
            ON account.tenant_id=auth_session.tenant_id AND account.id=auth_session.account_id
          JOIN public.tenants tenant ON tenant.id=auth_session.tenant_id
          WHERE auth_session.id=requested_auth_session_id
            AND auth_session.tenant_id=requested_tenant_id
            AND auth_session.revoked_at IS NULL AND auth_session.expires_at>statement_timestamp()
            AND auth_session.auth_version=account.auth_version AND account.is_active
            AND tenant.status='active'
            AND EXISTS(
              SELECT 1 FROM public.account_roles account_role
              JOIN public.role_permissions role_permission
                ON role_permission.tenant_id=account_role.tenant_id
               AND role_permission.role_id=account_role.role_id
              JOIN public.permissions permission
                ON permission.tenant_id=role_permission.tenant_id
               AND permission.id=role_permission.permission_id
              WHERE account_role.tenant_id=requested_tenant_id
                AND account_role.account_id=account.id AND permission.code='webhook:manage'
            );
          IF resolved_actor IS NULL OR resolved_actor IS DISTINCT FROM NULLIF(payload->>'actor_id','')::uuid THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce manager session or permission denied';
          END IF;
          event_name := CASE action_name WHEN 'connect' THEN 'connected'
            WHEN 'rotate_credential' THEN 'credential_rotated'
            WHEN 'revoke_credential' THEN 'credential_revoked'
            WHEN 'disconnect' THEN 'disconnected' ELSE NULL END;
          IF event_name IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce connection action';
          END IF;
          IF NULLIF(payload->>'idempotency_key','') IS NULL OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce idempotency evidence';
          END IF;
          SELECT event_type,payload_digest,connection_id INTO prior_event
          FROM public.commerce_connection_events
          WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior_event.event_type<>event_name OR prior_event.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce_idempotency_conflict';
            END IF;
            IF action_name IN ('connect','rotate_credential') THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='credential_secret_already_returned';
            END IF;
            RETURN prior_event.connection_id;
          END IF;

          IF action_name='connect' THEN
            IF EXISTS(SELECT 1 FROM public.commerce_connections
              WHERE tenant_id=requested_tenant_id AND status='active') THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='active_commerce_connection_exists';
            END IF;
            INSERT INTO public.commerce_connections(
              id,tenant_id,external_tenant_ref,external_shop_ref,base_url,capabilities,status,version,created_by
            ) VALUES (
              target_connection_id,requested_tenant_id,trim(payload->>'external_tenant_ref'),
              trim(payload->>'external_shop_ref'),payload->>'base_url',payload->'capabilities','active',1,
              resolved_actor
            );
            FOR credential_json IN SELECT value FROM jsonb_array_elements(payload->'credentials') LOOP
              INSERT INTO public.commerce_service_credentials(
                id,tenant_id,connection_id,direction,version,key_prefix,secret_ciphertext,valid_from,valid_until
              ) VALUES (
                (credential_json->>'id')::uuid,requested_tenant_id,target_connection_id,
                credential_json->>'direction',
                (credential_json->>'version')::integer,credential_json->>'key_prefix',
                decode(credential_json->>'secret_ciphertext','hex'),
                (credential_json->>'valid_from')::timestamptz,(credential_json->>'valid_until')::timestamptz
              );
            END LOOP;
          ELSE
            SELECT * INTO connection_row FROM public.commerce_connections
            WHERE tenant_id=requested_tenant_id AND id=target_connection_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='commerce_connection_not_found'; END IF;
            IF action_name<>'disconnect' AND connection_row.status<>'active' THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce_connection_not_active';
            END IF;
            IF action_name='rotate_credential' THEN
              credential_json := payload->'credential';
              IF EXISTS(
                SELECT 1 FROM public.commerce_service_credentials
                WHERE secret_ciphertext=decode(credential_json->>'secret_ciphertext','hex')
              ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce_credential_ciphertext_reuse';
              END IF;
              SELECT COALESCE(max(version),0) INTO current_version
              FROM public.commerce_service_credentials WHERE tenant_id=requested_tenant_id
                AND connection_id=connection_row.id AND direction=credential_json->>'direction';
              IF (credential_json->>'version')::integer<>current_version+1 THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce_credential_version_conflict';
              END IF;
              UPDATE public.commerce_service_credentials
              SET overlap_until=LEAST(valid_until,statement_timestamp()+interval '24 hours')
              WHERE tenant_id=requested_tenant_id AND connection_id=connection_row.id
                AND direction=credential_json->>'direction' AND version=current_version AND revoked_at IS NULL;
              INSERT INTO public.commerce_service_credentials(
                id,tenant_id,connection_id,direction,version,key_prefix,secret_ciphertext,valid_from,valid_until
              ) VALUES (
                (credential_json->>'id')::uuid,requested_tenant_id,connection_row.id,
                credential_json->>'direction',(credential_json->>'version')::integer,
                credential_json->>'key_prefix',decode(credential_json->>'secret_ciphertext','hex'),
                (credential_json->>'valid_from')::timestamptz,(credential_json->>'valid_until')::timestamptz
              );
            ELSIF action_name='revoke_credential' THEN
              UPDATE public.commerce_service_credentials SET revoked_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND connection_id=connection_row.id
                AND id=(payload->>'credential_id')::uuid AND revoked_at IS NULL;
              IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='commerce_credential_not_found'; END IF;
            ELSIF action_name='disconnect' AND connection_row.status='active' THEN
              UPDATE public.commerce_connections SET status='disconnected',disconnected_at=statement_timestamp(),
                version=version+1,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND id=connection_row.id;
              UPDATE public.commerce_service_credentials SET revoked_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND connection_id=connection_row.id AND revoked_at IS NULL;
            END IF;
          END IF;
          INSERT INTO public.commerce_connection_events(
            id,tenant_id,connection_id,event_type,idempotency_key,payload_digest,actor_id,details
          ) VALUES (
            (payload->>'event_id')::uuid,requested_tenant_id,target_connection_id,event_name,
            payload->>'idempotency_key',payload->>'payload_digest',resolved_actor,
            jsonb_strip_nulls(jsonb_build_object(
              'direction',payload#>>'{credential,direction}','credential_id',payload->>'credential_id'))
          );
          RETURN target_connection_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_commerce_connection_authority(uuid,jsonb) FROM PUBLIC")
    op.execute(
        r"""
        CREATE FUNCTION public.ensure_commerce_member_reference_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE reference_id uuid;
        BEGIN
          IF session_user<>'yimatong_callback'
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce member reference authority denied';
          END IF;
          IF NOT EXISTS(SELECT 1 FROM public.commerce_connections WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'connection_id')::uuid AND status='active')
             OR NOT EXISTS(SELECT 1 FROM public.brand_memberships WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'membership_id')::uuid AND status='active')
             OR NOT EXISTS(
               SELECT 1 FROM public.scan_events scan
               JOIN public.anonymous_visitors visitor
                 ON visitor.tenant_id=scan.tenant_id AND visitor.visitor_id=scan.visitor_id
               JOIN public.brand_membership_profile_links link
                 ON link.tenant_id=scan.tenant_id AND link.membership_id=(payload->>'membership_id')::uuid
               JOIN public.consumer_profiles profile
                 ON profile.tenant_id=link.tenant_id AND profile.id=link.consumer_profile_id
               WHERE scan.tenant_id=requested_tenant_id AND scan.id=(payload->>'scan_event_id')::uuid
                 AND scan.public_id=payload->>'public_id' AND scan.is_valid_visit
                 AND scan.scan_time>statement_timestamp()-interval '30 minutes'
                 AND profile.id=(payload->>'consumer_id')::uuid
                 AND visitor.consumer_id=profile.id
             ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce member reference scope invalid';
          END IF;
          SELECT id INTO reference_id FROM public.commerce_member_references
          WHERE tenant_id=requested_tenant_id AND connection_id=(payload->>'connection_id')::uuid
            AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
          IF FOUND THEN RETURN reference_id; END IF;
          reference_id := (payload->>'reference_id')::uuid;
          INSERT INTO public.commerce_member_references(
            id,tenant_id,connection_id,membership_id,member_ref
          ) VALUES (
            reference_id,requested_tenant_id,(payload->>'connection_id')::uuid,
            (payload->>'membership_id')::uuid,payload->>'member_ref'
          );
          RETURN reference_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM PUBLIC")
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_commerce_handoff_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          action_name text := payload->>'action';
          handoff_row public.commerce_identity_handoffs%ROWTYPE;
          member_reference_id_value uuid;
          member_ref_value text;
          prior_event record;
        BEGIN
          IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR session_user<>'yimatong_callback' THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce handoff authority denied';
          END IF;
          IF action_name NOT IN ('issue','redeem') OR NULLIF(payload->>'idempotency_key','') IS NULL
             OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce handoff request';
          END IF;
          SELECT event_type,payload_digest INTO prior_event FROM public.commerce_connection_events
          WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior_event.event_type<>(CASE action_name
                 WHEN 'issue' THEN 'handoff_issued' ELSE 'handoff_redeemed' END)
               OR prior_event.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce_idempotency_conflict';
            END IF;
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce_handoff_already_consumed';
          END IF;
          IF action_name='issue' THEN
            IF NOT EXISTS(SELECT 1 FROM public.commerce_connections WHERE tenant_id=requested_tenant_id
              AND id=(payload->>'connection_id')::uuid AND status='active')
               OR NOT EXISTS(SELECT 1 FROM public.brand_memberships WHERE tenant_id=requested_tenant_id
              AND id=(payload->>'membership_id')::uuid AND status='active')
               OR NOT EXISTS(
                 SELECT 1 FROM public.scan_events scan
                 JOIN public.anonymous_visitors visitor
                   ON visitor.tenant_id=scan.tenant_id AND visitor.visitor_id=scan.visitor_id
                 JOIN public.brand_membership_profile_links link
                   ON link.tenant_id=scan.tenant_id AND link.membership_id=(payload->>'membership_id')::uuid
                 JOIN public.consumer_profiles profile
                   ON profile.tenant_id=link.tenant_id AND profile.id=link.consumer_profile_id
                 WHERE scan.tenant_id=requested_tenant_id AND scan.id=(payload->>'scan_event_id')::uuid
                   AND scan.public_id=payload->>'public_id' AND scan.is_valid_visit
                   AND scan.scan_time>statement_timestamp()-interval '30 minutes'
                   AND profile.id=(payload->>'consumer_id')::uuid
                   AND visitor.consumer_id=profile.id
               ) THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce_handoff_scope_invalid';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(
              requested_tenant_id::text||':'||(payload->>'connection_id')||':'||(payload->>'membership_id'),0
            ));
            SELECT id,member_ref INTO member_reference_id_value,member_ref_value
            FROM public.commerce_member_references
            WHERE tenant_id=requested_tenant_id AND connection_id=(payload->>'connection_id')::uuid
              AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
            IF FOUND AND member_ref_value<>payload->>'member_ref' THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='commerce_member_reference_conflict';
            ELSIF NOT FOUND THEN
              member_reference_id_value := (payload->>'member_reference_id')::uuid;
              member_ref_value := payload->>'member_ref';
              INSERT INTO public.commerce_member_references(
                id,tenant_id,connection_id,membership_id,member_ref
              ) VALUES (
                member_reference_id_value,requested_tenant_id,(payload->>'connection_id')::uuid,
                (payload->>'membership_id')::uuid,member_ref_value
              );
            END IF;
            INSERT INTO public.commerce_identity_handoffs(
              id,tenant_id,connection_id,member_reference_id,token_digest,expires_at
            ) VALUES (
              (payload->>'handoff_id')::uuid,requested_tenant_id,(payload->>'connection_id')::uuid,
              member_reference_id_value,payload->>'token_digest',
              (payload->>'expires_at')::timestamptz
            );
            INSERT INTO public.commerce_integration_messages(
              id,tenant_id,connection_id,direction,message_id,message_version,message_type,
              occurred_at,payload,payload_digest,status,attempt_count
            ) VALUES (
              (payload->>'outbox_id')::uuid,requested_tenant_id,(payload->>'connection_id')::uuid,'outbox',
              'identity-handoff:'||(payload->>'handoff_id'),1,'identity.handoff.created',statement_timestamp(),
              jsonb_build_object('handoff_id',payload->>'handoff_id','member_ref',member_ref_value),
              encode(digest((payload->>'handoff_id')||':'||member_ref_value,'sha256'),'hex'),'pending',0
            );
          ELSE
            SELECT * INTO handoff_row FROM public.commerce_identity_handoffs
            WHERE tenant_id=requested_tenant_id AND id=(payload->>'handoff_id')::uuid
              AND connection_id=(payload->>'connection_id')::uuid FOR UPDATE;
            IF NOT FOUND OR handoff_row.token_digest<>payload->>'token_digest'
               OR handoff_row.expires_at<=statement_timestamp() THEN
              RAISE EXCEPTION USING ERRCODE='28000',MESSAGE='invalid_or_expired_commerce_handoff';
            END IF;
            IF handoff_row.redeemed_at IS NOT NULL THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce_handoff_already_redeemed';
            END IF;
            SELECT member_ref INTO member_ref_value FROM public.commerce_member_references
            WHERE tenant_id=requested_tenant_id AND id=handoff_row.member_reference_id
              AND connection_id=handoff_row.connection_id;
            IF member_ref_value<>payload->>'member_ref' OR NOT EXISTS(
              SELECT 1 FROM public.commerce_connections WHERE tenant_id=requested_tenant_id
                AND id=handoff_row.connection_id AND external_shop_ref=payload->>'target_shop' AND status='active'
            ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce_handoff_scope_mismatch'; END IF;
            UPDATE public.commerce_identity_handoffs SET redeemed_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=handoff_row.id;
          END IF;
          INSERT INTO public.commerce_connection_events(
            id,tenant_id,connection_id,event_type,idempotency_key,payload_digest,details
          ) VALUES (
            (payload->>'event_id')::uuid,requested_tenant_id,(payload->>'connection_id')::uuid,
            CASE action_name WHEN 'issue' THEN 'handoff_issued' ELSE 'handoff_redeemed' END,
            payload->>'idempotency_key',payload->>'payload_digest',
            jsonb_build_object('handoff_id',payload->>'handoff_id','member_ref',member_ref_value)
          );
          RETURN member_ref_value;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) FROM PUBLIC")
    op.execute(
        r"""
        CREATE FUNCTION public.accept_commerce_message_authority(requested_tenant_id uuid,request_payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          existing_row record;
          credential_row public.commerce_service_credentials%ROWTYPE;
          latest_version integer;
        BEGIN
          IF session_user<>'yimatong_callback'
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce message authority denied';
          END IF;
          SELECT id,payload_digest,message_type INTO existing_row FROM public.commerce_integration_messages
          WHERE tenant_id=requested_tenant_id AND connection_id=(request_payload->>'connection_id')::uuid
            AND direction='inbox' AND message_id=request_payload->>'message_id'
            AND message_version=(request_payload->>'message_version')::integer;
          IF FOUND THEN
            IF existing_row.payload_digest<>request_payload->>'payload_digest'
               OR existing_row.message_type<>request_payload->>'message_type' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce_event_idempotency_conflict';
            END IF;
            RETURN existing_row.id;
          END IF;
          SELECT * INTO credential_row FROM public.commerce_service_credentials
          WHERE tenant_id=requested_tenant_id AND id=(request_payload->>'credential_id')::uuid
            AND connection_id=(request_payload->>'connection_id')::uuid FOR UPDATE;
          IF NOT FOUND OR credential_row.direction<>'commerce_to_yimatong' OR credential_row.revoked_at IS NOT NULL
             OR credential_row.valid_from>statement_timestamp() OR credential_row.valid_until<=statement_timestamp()
             OR NOT EXISTS(SELECT 1 FROM public.commerce_connections WHERE tenant_id=requested_tenant_id
               AND id=credential_row.connection_id AND status='active') THEN
            RAISE EXCEPTION USING ERRCODE='28000',MESSAGE='expired_or_revoked_commerce_credential';
          END IF;
          SELECT max(version) INTO latest_version FROM public.commerce_service_credentials
          WHERE tenant_id=requested_tenant_id AND connection_id=credential_row.connection_id
            AND direction=credential_row.direction;
          IF credential_row.version<>latest_version
             AND (credential_row.overlap_until IS NULL OR credential_row.overlap_until<=statement_timestamp()) THEN
            RAISE EXCEPTION USING ERRCODE='28000',MESSAGE='expired_or_revoked_commerce_credential';
          END IF;
          IF NULLIF(request_payload#>>'{payload,member_ref}','') IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.commerce_member_references WHERE tenant_id=requested_tenant_id
              AND connection_id=credential_row.connection_id
              AND member_ref=request_payload#>>'{payload,member_ref}'
          ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce_member_reference_scope_mismatch'; END IF;
          INSERT INTO public.commerce_integration_messages(
            id,tenant_id,connection_id,direction,message_id,message_version,message_type,credential_id,
            occurred_at,payload,payload_digest,status,attempt_count,accepted_at
          ) VALUES (
            (request_payload->>'message_row_id')::uuid,requested_tenant_id,credential_row.connection_id,'inbox',
            request_payload->>'message_id',(request_payload->>'message_version')::integer,
            request_payload->>'message_type',credential_row.id,
            (request_payload->>'occurred_at')::timestamptz,request_payload->'payload',
            request_payload->>'payload_digest','accepted',0,
            statement_timestamp()
          );
          RETURN (request_payload->>'message_row_id')::uuid;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) FROM PUBLIC")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    _create_tables()
    _create_rls_and_guards()
    _create_authority_functions()
    op.execute(
        """
        DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN
          REVOKE ALL ON public.commerce_connections,public.commerce_service_credentials,
            public.commerce_member_references,public.commerce_identity_handoffs,
            public.commerce_integration_messages,public.commerce_connection_events FROM yimatong_app;
          GRANT SELECT ON public.commerce_connections,public.commerce_service_credentials,
            public.commerce_member_references,public.commerce_identity_handoffs,
            public.commerce_integration_messages,public.commerce_connection_events TO yimatong_app;
          REVOKE SELECT ON public.commerce_service_credentials,public.commerce_identity_handoffs
            FROM yimatong_app;
          GRANT SELECT(id,tenant_id,connection_id,direction,version,key_prefix,valid_from,valid_until,
            overlap_until,revoked_at,created_at) ON public.commerce_service_credentials TO yimatong_app;
          GRANT SELECT(id,tenant_id,connection_id,member_reference_id,expires_at,redeemed_at,created_at)
            ON public.commerce_identity_handoffs TO yimatong_app;
          REVOKE ALL ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) FROM yimatong_app;
          GRANT EXECUTE ON FUNCTION public.mutate_commerce_connection_authority(uuid,jsonb) TO yimatong_app;
          REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM yimatong_app;
          REVOKE ALL ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) FROM yimatong_app;
        END IF; END $do$;
        """
    )
    op.execute(
        """
        DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_callback') THEN
          REVOKE ALL ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) FROM yimatong_callback;
          REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM yimatong_callback;
          REVOKE ALL ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) FROM yimatong_callback;
          GRANT EXECUTE ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) TO yimatong_callback;
          GRANT EXECUTE ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) TO yimatong_callback;
        END IF; END $do$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $do$ BEGIN
          IF EXISTS(SELECT 1 FROM public.commerce_connections LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_service_credentials LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_member_references LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_identity_handoffs LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_integration_messages LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_connection_events LIMIT 1) THEN
            RAISE EXCEPTION USING ERRCODE='55000',
              MESSAGE='cannot downgrade commerce integration authority while durable facts exist; use forward recovery';
          END IF;
        END $do$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS public.accept_commerce_message_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.mutate_commerce_handoff_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.ensure_commerce_member_reference_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.mutate_commerce_connection_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.guard_commerce_authority_rows() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.guard_commerce_connection_event() CASCADE")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE public.{table}")
