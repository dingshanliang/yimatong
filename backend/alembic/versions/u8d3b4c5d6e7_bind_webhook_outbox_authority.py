"""bind webhook outbox authority

Revision ID: u8d3b4c5d6e7
Revises: u8d2a3b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8d3b4c5d6e7"
down_revision: str | Sequence[str] | None = "u8d2a3b4c5d6"
branch_labels = None
depends_on = None

_FUNCTIONS = (
    "delete_webhook_endpoint(uuid,uuid,uuid,integer)",
    "update_webhook_endpoint(uuid,uuid,uuid,integer,text,jsonb,text,boolean,boolean,integer,bytea,bytea,text)",
    "create_webhook_endpoint(uuid,uuid,uuid,text,jsonb,text,boolean,integer,bytea,bytea,text)",
    "assert_webhook_admin(uuid,uuid,text)",
)


def _runtime_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar_one())


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")
    op.execute("LOCK TABLE public.webhook_endpoints IN SHARE ROW EXCLUSIVE MODE")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT uq_webhook_endpoints_tenant_id_id UNIQUE USING INDEX uq_webhook_endpoints_tenant_id_id_idx")
    op.execute("ALTER TABLE webhook_domain_events ADD CONSTRAINT uq_webhook_domain_events_tenant_id_id UNIQUE USING INDEX uq_webhook_domain_events_tenant_id_id_idx")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_config_version CHECK (config_version>=1) NOT VALID")
    op.execute("ALTER TABLE webhook_domain_events ADD CONSTRAINT ck_webhook_domain_events_payload_digest CHECK (payload_digest ~ '^[0-9a-f]{64}$') NOT VALID")
    op.execute("ALTER TABLE webhook_domain_events ADD CONSTRAINT ck_webhook_domain_events_uuid7 CHECK ((get_byte(uuid_send(id),6)>>4)=7) NOT VALID")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT fk_webhook_deliveries_tenant_endpoint FOREIGN KEY(tenant_id,endpoint_id) REFERENCES webhook_endpoints(tenant_id,id) NOT VALID")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT fk_webhook_deliveries_tenant_domain_event FOREIGN KEY(tenant_id,domain_event_id) REFERENCES webhook_domain_events(tenant_id,id) NOT VALID")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_snapshot CHECK (domain_event_id IS NULL OR (payload_digest IS NOT NULL AND endpoint_url IS NOT NULL AND endpoint_secret_ciphertext IS NOT NULL AND endpoint_secret_nonce IS NOT NULL AND endpoint_secret_key_id IS NOT NULL AND endpoint_config_version IS NOT NULL)) NOT VALID")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_lease_pair CHECK ((lease_token IS NULL)=(lease_expires_at IS NULL)) NOT VALID")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_attempts CHECK (attempt_count>=0 AND retry_count>=0) NOT VALID")
    for constraint, table in (
        ("ck_webhook_endpoints_config_version", "webhook_endpoints"),
        ("ck_webhook_domain_events_payload_digest", "webhook_domain_events"),
        ("ck_webhook_domain_events_uuid7", "webhook_domain_events"),
        ("fk_webhook_deliveries_tenant_endpoint", "webhook_deliveries"),
        ("fk_webhook_deliveries_tenant_domain_event", "webhook_deliveries"),
        ("ck_webhook_deliveries_snapshot", "webhook_deliveries"),
        ("ck_webhook_deliveries_lease_pair", "webhook_deliveries"),
        ("ck_webhook_deliveries_attempts", "webhook_deliveries"),
    ):
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")
    for column in ("secret_ciphertext", "secret_nonce", "secret_key_id", "config_version"):
        op.alter_column("webhook_endpoints", column, nullable=False)

    op.execute(r"""CREATE FUNCTION public.guard_webhook_domain_event() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
      IF TG_OP='DELETE' OR OLD.id IS DISTINCT FROM NEW.id OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
         OR OLD.event_type IS DISTINCT FROM NEW.event_type OR OLD.payload IS DISTINCT FROM NEW.payload
         OR OLD.payload_digest IS DISTINCT FROM NEW.payload_digest OR OLD.occurred_at IS DISTINCT FROM NEW.occurred_at
         OR OLD.created_at IS DISTINCT FROM NEW.created_at OR OLD.expanded_at IS NOT NULL
         OR NEW.expanded_at IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='webhook domain event is immutable';
      END IF; RETURN NEW; END $fn$""")
    op.execute("REVOKE ALL ON FUNCTION public.guard_webhook_domain_event() FROM PUBLIC")
    op.execute("CREATE TRIGGER trg_guard_webhook_domain_event BEFORE UPDATE OR DELETE ON webhook_domain_events FOR EACH ROW EXECUTE FUNCTION guard_webhook_domain_event()")
    op.execute(r"""CREATE FUNCTION public.guard_webhook_delivery_snapshot() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ DECLARE event_row record;endpoint_row record; BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='webhook delivery is durable'; END IF;
      IF TG_OP='UPDATE' AND (OLD.id IS DISTINCT FROM NEW.id OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
        OR OLD.endpoint_id IS DISTINCT FROM NEW.endpoint_id OR OLD.event_id IS DISTINCT FROM NEW.event_id
        OR OLD.domain_event_id IS DISTINCT FROM NEW.domain_event_id OR OLD.event_type IS DISTINCT FROM NEW.event_type
        OR OLD.payload::jsonb IS DISTINCT FROM NEW.payload::jsonb OR OLD.payload_digest IS DISTINCT FROM NEW.payload_digest
        OR OLD.endpoint_url IS DISTINCT FROM NEW.endpoint_url
        OR OLD.endpoint_secret_ciphertext IS DISTINCT FROM NEW.endpoint_secret_ciphertext
        OR OLD.endpoint_secret_nonce IS DISTINCT FROM NEW.endpoint_secret_nonce
        OR OLD.endpoint_secret_key_id IS DISTINCT FROM NEW.endpoint_secret_key_id
        OR OLD.endpoint_config_version IS DISTINCT FROM NEW.endpoint_config_version
        OR OLD.created_at IS DISTINCT FROM NEW.created_at) THEN
        RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='webhook delivery snapshot is immutable'; END IF;
      IF TG_OP='INSERT' AND NEW.domain_event_id IS NOT NULL THEN
        SELECT * INTO event_row FROM webhook_domain_events WHERE tenant_id=NEW.tenant_id AND id=NEW.domain_event_id;
        SELECT * INTO endpoint_row FROM webhook_endpoints WHERE tenant_id=NEW.tenant_id AND id=NEW.endpoint_id;
        IF event_row.id IS NULL OR endpoint_row.id IS NULL OR NEW.event_id<>event_row.id::text
          OR NEW.event_type<>event_row.event_type OR NEW.payload::jsonb IS DISTINCT FROM event_row.payload
          OR NEW.payload_digest<>event_row.payload_digest OR NEW.endpoint_url<>endpoint_row.url
          OR NEW.endpoint_secret_ciphertext IS DISTINCT FROM endpoint_row.secret_ciphertext
          OR NEW.endpoint_secret_nonce IS DISTINCT FROM endpoint_row.secret_nonce
          OR NEW.endpoint_secret_key_id<>endpoint_row.secret_key_id
          OR NEW.endpoint_config_version<>endpoint_row.config_version THEN
          RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='webhook delivery snapshot does not match authority'; END IF;
      END IF; RETURN NEW; END $fn$""")
    op.execute("REVOKE ALL ON FUNCTION public.guard_webhook_delivery_snapshot() FROM PUBLIC")
    op.execute("CREATE TRIGGER trg_guard_webhook_delivery_snapshot BEFORE INSERT OR UPDATE OR DELETE ON webhook_deliveries FOR EACH ROW EXECUTE FUNCTION guard_webhook_delivery_snapshot()")

    op.execute(r"""CREATE FUNCTION public.assert_webhook_admin(requested_tenant_id uuid,requested_auth_session_id uuid,requested_permission text)
    RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE actor uuid; now_at timestamptz:=CURRENT_TIMESTAMP; BEGIN
      IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
         OR requested_permission NOT IN ('webhook:read','webhook:manage') THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='webhook authority denied'; END IF;
      IF NOT pg_try_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_auth_session_id::text,0)) THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='webhook auth session is busy'; END IF;
      SELECT account.id INTO actor FROM auth_sessions session
      JOIN accounts account ON account.tenant_id=session.tenant_id AND account.id=session.account_id
      JOIN tenants tenant ON tenant.id=session.tenant_id
      WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
        AND session.revoked_at IS NULL AND session.expires_at>now_at
        AND NULLIF(btrim(session.current_refresh_jti),'') IS NOT NULL
        AND session.auth_version=account.auth_version AND account.is_active
        AND tenant.status='active' AND tenant.tenant_type='brand';
      IF actor IS NULL OR NOT EXISTS(SELECT 1 FROM account_roles ar
        JOIN roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
        JOIN role_permissions rp ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
        JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
        WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=actor
          AND role.name='admin' AND permission.code=requested_permission) THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='webhook permission denied'; END IF;
      RETURN actor;
    END $fn$""")

    op.execute(r"""CREATE FUNCTION public.create_webhook_endpoint(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_endpoint_id uuid,requested_url text,
      requested_events jsonb,requested_description text,requested_batch_mode boolean,requested_batch_size integer,
      requested_secret_ciphertext bytea,requested_secret_nonce bytea,requested_secret_key_id text)
    RETURNS TABLE(endpoint_id uuid,config_version integer) LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public AS $fn$ BEGIN
      PERFORM assert_webhook_admin(requested_tenant_id,requested_auth_session_id,'webhook:manage');
      IF requested_endpoint_id IS NULL OR (get_byte(uuid_send(requested_endpoint_id),6)>>4)<>7
         OR length(requested_url) NOT BETWEEN 9 AND 500 OR requested_url<>btrim(requested_url)
         OR requested_url !~ '^https://[^[:space:]]+$'
         OR jsonb_typeof(requested_events)<>'array' OR jsonb_array_length(requested_events) NOT BETWEEN 1 AND 9
         OR NOT requested_events <@ '["scan.created","claim.created","risk.alert","campaign.active","campaign.paused","campaign.ended"]'::jsonb
         OR requested_batch_size NOT BETWEEN 1 AND 1000 OR octet_length(requested_secret_ciphertext)<17
         OR octet_length(requested_secret_nonce)<>12 OR requested_secret_key_id !~ '^[A-Za-z0-9._:-]{1,64}$' THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid webhook endpoint'; END IF;
      INSERT INTO webhook_endpoints(id,tenant_id,url,description,events,secret_ciphertext,secret_nonce,secret_key_id,
        enabled,batch_mode,batch_size,config_version) VALUES(requested_endpoint_id,requested_tenant_id,requested_url,
        NULLIF(btrim(requested_description),''),requested_events,requested_secret_ciphertext,requested_secret_nonce,
        requested_secret_key_id,true,requested_batch_mode,requested_batch_size,1);
      RETURN QUERY SELECT requested_endpoint_id,1;
    END $fn$""")

    op.execute(r"""CREATE FUNCTION public.update_webhook_endpoint(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_endpoint_id uuid,requested_expected_version integer,
      requested_url text,requested_events jsonb,requested_description text,requested_enabled boolean,
      requested_batch_mode boolean,requested_batch_size integer,requested_secret_ciphertext bytea,
      requested_secret_nonce bytea,requested_secret_key_id text)
    RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE next_version integer; BEGIN
      PERFORM assert_webhook_admin(requested_tenant_id,requested_auth_session_id,'webhook:manage');
      PERFORM pg_advisory_xact_lock(hashtextextended('webhook-endpoint:'||requested_tenant_id::text||':'||requested_endpoint_id::text,0));
      IF length(requested_url) NOT BETWEEN 9 AND 500 OR requested_url<>btrim(requested_url)
         OR requested_url !~ '^https://[^[:space:]]+$' OR jsonb_typeof(requested_events)<>'array'
         OR jsonb_array_length(requested_events) NOT BETWEEN 1 AND 9
         OR NOT requested_events <@ '["scan.created","claim.created","risk.alert","campaign.active","campaign.paused","campaign.ended"]'::jsonb
         OR requested_batch_size NOT BETWEEN 1 AND 1000
         OR ((requested_secret_ciphertext IS NULL OR requested_secret_nonce IS NULL OR requested_secret_key_id IS NULL)
           AND NOT (requested_secret_ciphertext IS NULL AND requested_secret_nonce IS NULL AND requested_secret_key_id IS NULL))
         OR (requested_secret_ciphertext IS NOT NULL AND (octet_length(requested_secret_ciphertext)<17
           OR octet_length(requested_secret_nonce)<>12 OR requested_secret_key_id !~ '^[A-Za-z0-9._:-]{1,64}$')) THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid webhook endpoint'; END IF;
      UPDATE webhook_endpoints SET url=requested_url,events=requested_events,description=NULLIF(btrim(requested_description),''),
        enabled=requested_enabled,batch_mode=requested_batch_mode,batch_size=requested_batch_size,
        secret_ciphertext=COALESCE(requested_secret_ciphertext,secret_ciphertext),
        secret_nonce=COALESCE(requested_secret_nonce,secret_nonce),secret_key_id=COALESCE(requested_secret_key_id,secret_key_id),
        config_version=config_version+1,updated_at=CURRENT_TIMESTAMP
      WHERE tenant_id=requested_tenant_id AND id=requested_endpoint_id AND config_version=requested_expected_version
      RETURNING config_version INTO next_version;
      IF next_version IS NULL THEN RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='webhook endpoint version conflict'; END IF;
      RETURN next_version;
    END $fn$""")

    op.execute(r"""CREATE FUNCTION public.delete_webhook_endpoint(requested_tenant_id uuid,requested_auth_session_id uuid,
      requested_endpoint_id uuid,requested_expected_version integer) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,public AS $fn$ DECLARE removed boolean; BEGIN
      PERFORM assert_webhook_admin(requested_tenant_id,requested_auth_session_id,'webhook:manage');
      PERFORM pg_advisory_xact_lock(hashtextextended('webhook-endpoint:'||requested_tenant_id::text||':'||requested_endpoint_id::text,0));
      DELETE FROM webhook_endpoints WHERE tenant_id=requested_tenant_id AND id=requested_endpoint_id
        AND config_version=requested_expected_version RETURNING true INTO removed;
      IF removed IS NOT TRUE THEN RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='webhook endpoint version conflict'; END IF;
      RETURN true; END $fn$""")

    for signature in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _runtime_exists():
        for signature in _FUNCTIONS:
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")
        op.execute("REVOKE INSERT,UPDATE,DELETE ON webhook_endpoints FROM yimatong_app")
        op.execute("GRANT SELECT ON webhook_endpoints TO yimatong_app")
        op.execute("GRANT SELECT,INSERT,UPDATE ON webhook_domain_events TO yimatong_app")
        op.execute("REVOKE DELETE ON webhook_domain_events FROM yimatong_app")
        op.execute("GRANT SELECT,INSERT,UPDATE ON webhook_deliveries TO yimatong_app")
        op.execute("REVOKE DELETE ON webhook_deliveries FROM yimatong_app")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    for signature in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_webhook_delivery_snapshot ON webhook_deliveries")
    op.execute("DROP FUNCTION IF EXISTS guard_webhook_delivery_snapshot()")
    op.execute("DROP TRIGGER trg_guard_webhook_domain_event ON webhook_domain_events")
    op.execute("DROP FUNCTION guard_webhook_domain_event()")
    for constraint in (
        "ck_webhook_deliveries_attempts", "ck_webhook_deliveries_lease_pair", "ck_webhook_deliveries_snapshot",
        "fk_webhook_deliveries_tenant_domain_event", "fk_webhook_deliveries_tenant_endpoint",
    ):
        op.drop_constraint(constraint, "webhook_deliveries")
    op.drop_constraint("ck_webhook_domain_events_uuid7", "webhook_domain_events")
    op.drop_constraint("ck_webhook_domain_events_payload_digest", "webhook_domain_events")
    op.drop_constraint("uq_webhook_domain_events_tenant_id_id", "webhook_domain_events")
    op.drop_constraint("ck_webhook_endpoints_config_version", "webhook_endpoints")
    op.drop_constraint("uq_webhook_endpoints_tenant_id_id", "webhook_endpoints")
    # UNIQUE USING INDEX transfers ownership to the constraint, so dropping
    # the constraint also drops the u8d2 index. Recreate the exact parent
    # artifacts before returning control to the previous revision.
    op.execute("CREATE UNIQUE INDEX uq_webhook_endpoints_tenant_id_id_idx ON webhook_endpoints(tenant_id,id)")
    op.execute("CREATE UNIQUE INDEX uq_webhook_domain_events_tenant_id_id_idx ON webhook_domain_events(tenant_id,id)")
    for column in ("config_version", "secret_key_id", "secret_nonce", "secret_ciphertext"):
        op.alter_column("webhook_endpoints", column, nullable=True)
