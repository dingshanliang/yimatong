"""bind confirmed GMV attribution to immutable consumer scan evidence

Revision ID: u8b2e3f4a5b6
Revises: u8b1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8b2e3f4a5b6"
down_revision: str | Sequence[str] | None = "u8b1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "confirm_gmv_attribution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text)"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.create_table(
        "gmv_attribution_confirmations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("attribution_id", sa.Uuid(), nullable=False),
        sa.Column("external_order_id", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("actor_account_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_id", sa.Uuid(), nullable=False),
        sa.Column("scan_event_id", sa.Uuid(), nullable=False),
        # scan_events is partitioned by scan_time and its primary key is
        # (id, scan_time), so the exact partition key is part of the evidence.
        sa.Column("scan_event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visitor_id", sa.String(64), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attribution_window_hours", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("provenance_digest", sa.String(64), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "attribution_id"],
            ["gmv_attributions.tenant_id", "gmv_attributions.id"],
            name="fk_gmv_attr_confirmations_tenant_attribution_u8b",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_order_id"],
            ["external_orders.tenant_id", "external_orders.id"],
            name="fk_gmv_attr_confirmations_tenant_order_u8b",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "auth_session_id"],
            ["auth_sessions.tenant_id", "auth_sessions.id"],
            name="fk_gmv_attr_confirmations_tenant_auth_session_u8b",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_gmv_attr_confirmations_tenant_actor_u8b",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "consumer_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_gmv_attr_confirmations_tenant_consumer_u8b",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "public_id"],
            ["code_items.tenant_id", "code_items.public_id"],
            name="fk_gmv_attr_confirmations_tenant_public_id_u8b",
        ),
        sa.UniqueConstraint("tenant_id", "attribution_id", name="uq_gmv_attr_confirmations_tenant_attr_u8b"),
        sa.UniqueConstraint("tenant_id", "external_order_id", name="uq_gmv_attr_confirmations_tenant_order_u8b"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_gmv_attr_confirmations_tenant_idem_u8b"),
        sa.CheckConstraint("attribution_window_hours IN (168,720)", name="ck_gmv_attr_confirmations_window_u8b"),
        sa.CheckConstraint("window_started_at=scan_received_at", name="ck_gmv_attr_confirmations_window_start_u8b"),
        sa.CheckConstraint(
            "window_ends_at=window_started_at + make_interval(hours=>attribution_window_hours)",
            name="ck_gmv_attr_confirmations_window_end_u8b",
        ),
        sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_gmv_attr_confirmations_payload_u8b"),
        sa.CheckConstraint(
            "provenance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_gmv_attr_confirmations_provenance_u8b",
        ),
        sa.CheckConstraint("NULLIF(btrim(visitor_id),'') IS NOT NULL", name="ck_gmv_attr_confirmations_visitor_u8b"),
    )
    op.create_index(
        "ix_gmv_attr_confirmations_cohort_u8b",
        "gmv_attribution_confirmations",
        ["tenant_id", "window_started_at", "window_ends_at"],
    )
    op.execute("ALTER TABLE public.gmv_attribution_confirmations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.gmv_attribution_confirmations FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY tenant_isolation ON public.gmv_attribution_confirmations
        USING (tenant_id=public.current_tenant_id() OR
          (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true'
           AND has_parameter_privilege(current_user,'app.bypass_rls','SET')))
        WITH CHECK (tenant_id=public.current_tenant_id() OR
          (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true'
           AND has_parameter_privilege(current_user,'app.bypass_rls','SET')))"""
    )
    op.execute("REVOKE ALL PRIVILEGES ON public.gmv_attribution_confirmations FROM PUBLIC")

    op.execute(
        """CREATE FUNCTION public.guard_gmv_attribution_confirmation_u8b() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
          RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='GMV attribution confirmation evidence is immutable';
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_gmv_attribution_confirmation_u8b() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_gmv_attribution_confirmation_u8b "
        "BEFORE UPDATE OR DELETE ON public.gmv_attribution_confirmations "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_gmv_attribution_confirmation_u8b()"
    )
    op.execute(
        """CREATE FUNCTION public.guard_confirmed_gmv_attribution_u8b() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
          IF OLD.authority_status='confirmed' AND (
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.external_order_id IS DISTINCT FROM OLD.external_order_id
             OR NEW.public_id IS DISTINCT FROM OLD.public_id OR NEW.code_item_id IS DISTINCT FROM OLD.code_item_id
             OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR NEW.consumer_id IS DISTINCT FROM OLD.consumer_id
             OR NEW.match_type IS DISTINCT FROM OLD.match_type OR NEW.scan_time IS DISTINCT FROM OLD.scan_time
             OR NEW.attribution_window_hours IS DISTINCT FROM OLD.attribution_window_hours
             OR NEW.product_id IS DISTINCT FROM OLD.product_id OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
             OR NEW.channel_snapshot IS DISTINCT FROM OLD.channel_snapshot
             OR NEW.page_version_id IS DISTINCT FROM OLD.page_version_id
             OR NEW.original_amount IS DISTINCT FROM OLD.original_amount
             OR NEW.authority_status IS DISTINCT FROM OLD.authority_status) THEN
            RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='confirmed GMV attribution provenance is immutable';
          END IF;
          RETURN NEW;
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_confirmed_gmv_attribution_u8b() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_confirmed_gmv_attribution_u8b "
        "BEFORE UPDATE ON public.gmv_attributions FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_confirmed_gmv_attribution_u8b()"
    )

    op.execute(
        r"""CREATE FUNCTION public.confirm_gmv_attribution(
          requested_tenant_id uuid,requested_auth_session_id uuid,
          requested_attribution_id uuid,requested_confirmation_id uuid,
          requested_order_id uuid,requested_consumer_id uuid,
          requested_scan_event_id uuid,requested_scan_time timestamptz,requested_window_hours integer,
          requested_idempotency_key text,requested_payload_digest text)
        RETURNS TABLE(attribution_id uuid,authority_status text,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE prior gmv_attribution_confirmations%ROWTYPE; target_order external_orders%ROWTYPE;
          target_consumer consumer_profiles%ROWTYPE; target_scan scan_events%ROWTYPE;
          target_visitor anonymous_visitors%ROWTYPE; target_code code_items%ROWTYPE;
          target_batch code_batches%ROWTYPE; received_at timestamptz;
          ends_at timestamptz; provenance text; resolved_actor_id uuid;
          probed_session_tenant_id uuid; now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='GMV attribution authority denied'; END IF;
          IF requested_attribution_id IS NULL OR requested_confirmation_id IS NULL
             OR (get_byte(uuid_send(requested_attribution_id),6) >> 4)<>7
             OR (get_byte(uuid_send(requested_confirmation_id),6) >> 4)<>7
             OR requested_order_id IS NULL OR requested_consumer_id IS NULL OR requested_scan_event_id IS NULL
             OR requested_scan_time IS NULL OR requested_window_hours NOT IN (168,720)
             OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
             OR length(requested_idempotency_key)>128 OR requested_payload_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid GMV attribution request'; END IF;

          IF NOT pg_try_advisory_xact_lock_shared(
            hashtextextended('auth-session:'||requested_auth_session_id::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='GMV attribution auth session is busy'; END IF;
          SELECT session.tenant_id INTO probed_session_tenant_id FROM public.auth_sessions session
           WHERE session.id=requested_auth_session_id;
          IF NOT FOUND OR probed_session_tenant_id IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='GMV attribution auth session is missing'; END IF;
          SELECT account.id INTO resolved_actor_id
          FROM public.auth_sessions session JOIN public.accounts account
            ON account.tenant_id=session.tenant_id AND account.id=session.account_id
          JOIN public.tenants tenant ON tenant.id=session.tenant_id
          WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
            AND session.revoked_at IS NULL AND session.expires_at>now_at
            AND NULLIF(btrim(session.current_refresh_jti),'') IS NOT NULL
            AND session.auth_version=account.auth_version AND account.is_active
            AND tenant.status='active' AND tenant.tenant_type='brand';
          IF resolved_actor_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='GMV attribution auth session is not live'; END IF;
          IF NOT EXISTS(SELECT 1 FROM public.account_roles ar JOIN public.roles role
              ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN public.role_permissions rp ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
            JOIN public.permissions permission
              ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=resolved_actor_id
              AND role.name IN ('admin','operator') AND permission.code='order:manage') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='GMV attribution order manage permission denied'; END IF;

          PERFORM pg_advisory_xact_lock(hashtextextended(
            'gmv-attribution:'||requested_tenant_id::text||':'||requested_order_id::text,0));
          SELECT * INTO prior FROM public.gmv_attribution_confirmations c
           WHERE c.tenant_id=requested_tenant_id AND c.idempotency_key=requested_idempotency_key;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest OR prior.external_order_id<>requested_order_id
               OR prior.consumer_id<>requested_consumer_id OR prior.scan_event_id<>requested_scan_event_id
               OR prior.scan_event_time<>requested_scan_time
               OR prior.attribution_window_hours<>requested_window_hours THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='GMV attribution idempotency payload conflicts'; END IF;
            RETURN QUERY SELECT prior.attribution_id,'confirmed'::text,true; RETURN;
          END IF;

          SELECT * INTO target_order FROM public.external_orders o
           WHERE o.tenant_id=requested_tenant_id AND o.id=requested_order_id FOR UPDATE;
          IF NOT FOUND OR target_order.ledger_net_amount IS NULL OR target_order.order_time IS NULL
             OR target_order.phone_hash IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='eligible confirmed order is missing'; END IF;
          IF target_order.matched OR EXISTS(SELECT 1 FROM public.gmv_attribution_confirmations c
             WHERE c.tenant_id=requested_tenant_id AND c.external_order_id=requested_order_id) THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='order already has a confirmed attribution'; END IF;

          SELECT * INTO target_consumer FROM public.consumer_profiles c
           WHERE c.tenant_id=requested_tenant_id AND c.id=requested_consumer_id FOR SHARE;
          IF NOT FOUND OR target_consumer.phone_hash IS DISTINCT FROM target_order.phone_hash
             OR target_consumer.lead_contact_suppressed THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='order consumer evidence is missing'; END IF;
          SELECT * INTO target_scan FROM public.scan_events s
           WHERE s.tenant_id=requested_tenant_id AND s.id=requested_scan_event_id
             AND s.scan_time=requested_scan_time AND s.is_valid_visit IS TRUE FOR SHARE;
          IF NOT FOUND OR target_scan.visitor_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='valid scan evidence is missing'; END IF;
          SELECT * INTO target_visitor FROM public.anonymous_visitors v
           WHERE v.tenant_id=requested_tenant_id AND v.visitor_id=target_scan.visitor_id
             AND v.consumer_id=requested_consumer_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='consumer scan linkage is missing'; END IF;

          received_at:=target_scan.created_at;
          ends_at:=received_at+make_interval(hours=>requested_window_hours);
          IF target_order.order_time<received_at OR target_order.order_time>ends_at THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='order falls outside attribution cohort window'; END IF;
          SELECT * INTO target_code FROM public.code_items ci
           WHERE ci.tenant_id=requested_tenant_id AND ci.public_id=target_scan.public_id;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='scan code evidence is missing'; END IF;
          SELECT * INTO target_batch FROM public.code_batches b
           WHERE b.tenant_id=requested_tenant_id AND b.id=target_code.code_batch_id;
          provenance:=encode(digest(convert_to(
            requested_tenant_id::text||':'||requested_order_id::text||':'||requested_consumer_id::text||':'||
            requested_auth_session_id::text||':'||resolved_actor_id::text||':'||
            requested_scan_event_id::text||':'||requested_scan_time::text||':'||received_at::text||':'||
            requested_window_hours::text||':'||requested_payload_digest,'UTF8'),'sha256'),'hex');

          INSERT INTO public.gmv_attributions(id,tenant_id,external_order_id,public_id,code_item_id,campaign_id,
            consumer_id,amount,match_type,scan_time,attribution_window_hours,confidence_score,product_id,
            code_batch_id,channel_snapshot,page_version_id,original_amount,authority_status)
          VALUES(requested_attribution_id,requested_tenant_id,requested_order_id,target_scan.public_id,target_code.id,NULL,
            requested_consumer_id,target_order.ledger_net_amount::float8,'verified_consumer_scan',received_at,
            requested_window_hours,1.0,target_batch.product_id,target_code.code_batch_id,target_order.channel,NULL,
            target_order.ledger_original_amount::float8,'confirmed');
          INSERT INTO public.gmv_attribution_confirmations(id,tenant_id,attribution_id,external_order_id,
            auth_session_id,actor_account_id,consumer_id,
            scan_event_id,scan_event_time,scan_received_at,visitor_id,public_id,window_started_at,window_ends_at,
            attribution_window_hours,idempotency_key,payload_digest,provenance_digest)
          VALUES(requested_confirmation_id,requested_tenant_id,requested_attribution_id,requested_order_id,
            requested_auth_session_id,resolved_actor_id,requested_consumer_id,
            requested_scan_event_id,requested_scan_time,received_at,target_scan.visitor_id,target_scan.public_id,
            received_at,ends_at,requested_window_hours,requested_idempotency_key,requested_payload_digest,provenance);
          UPDATE public.external_orders SET matched=true,updated_at=statement_timestamp()
           WHERE tenant_id=requested_tenant_id AND id=requested_order_id;
          RETURN QUERY SELECT requested_attribution_id,'confirmed'::text,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC")
    op.execute(
        f"DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON public.gmv_attributions FROM yimatong_app; "
        "REVOKE ALL PRIVILEGES ON public.gmv_attribution_confirmations FROM yimatong_app; "
        "GRANT SELECT ON public.gmv_attributions,public.gmv_attribution_confirmations TO yimatong_app; "
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION} TO yimatong_app; END IF; END $do$"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    confirmed = op.get_bind().execute(sa.text("SELECT count(*) FROM gmv_attribution_confirmations")).scalar_one()
    if confirmed:
        raise RuntimeError("u8b2 downgrade blocked: immutable confirmed attribution evidence exists")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_confirmed_gmv_attribution_u8b ON public.gmv_attributions")
    op.execute("DROP FUNCTION IF EXISTS public.guard_confirmed_gmv_attribution_u8b()")
    op.drop_table("gmv_attribution_confirmations")
    op.execute("DROP FUNCTION IF EXISTS public.guard_gmv_attribution_confirmation_u8b()")
    op.execute(
        "DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        "GRANT SELECT,INSERT,UPDATE,DELETE ON public.gmv_attributions TO yimatong_app; END IF; END $do$"
    )
