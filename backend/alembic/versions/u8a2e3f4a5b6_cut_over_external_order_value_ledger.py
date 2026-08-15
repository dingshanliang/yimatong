"""cut over external order value ledger authority

Revision ID: u8a2e3f4a5b6
Revises: u8a1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8a2e3f4a5b6"
down_revision: str | Sequence[str] | None = "u8a1d2e3f4a5"
branch_labels = None
depends_on = None

_FUNCTION = (
    "record_external_order_value_event(uuid,text,text,text,numeric,text,text,text,text,uuid,"
    "timestamp with time zone,timestamp with time zone,text,text,text,text)"
)
_DOWNGRADE_BLOCKING_FACTS = """
SELECT
  (SELECT count(*) FROM external_order_ledger_recovery_markers)
  + (SELECT count(*) FROM external_order_value_events e
     WHERE e.provenance_type<>'backfill' OR e.actor_type<>'migration'
        OR e.actor_id IS NOT NULL OR e.provenance_verified
        OR e.event_type NOT IN ('order_confirmed','refund')
        OR e.sequence_no<>CASE e.event_type WHEN 'order_confirmed' THEN 1 ELSE 2 END
        OR NOT EXISTS (
          SELECT 1 FROM external_order_value_receipts r
          WHERE r.tenant_id=e.tenant_id AND r.id=e.receipt_id AND r.event_id=e.id
            AND r.order_id=e.order_id AND r.event_type=e.event_type
            AND r.source_system=e.source_system AND r.external_order_id=e.external_order_id
            AND e.provenance_digest=r.payload_digest
            AND r.idempotency_key=(CASE e.event_type
              WHEN 'order_confirmed' THEN 'backfill:confirmed:' ELSE 'backfill:refund:' END)||e.order_id::text))
  + (SELECT count(*) FROM external_order_value_receipts r
     WHERE NOT EXISTS (
       SELECT 1 FROM external_order_value_events e
       WHERE e.tenant_id=r.tenant_id AND e.id=r.event_id AND e.receipt_id=r.id
         AND e.order_id=r.order_id AND e.event_type=r.event_type
         AND e.provenance_type='backfill' AND e.actor_type='migration'
         AND e.actor_id IS NULL AND NOT e.provenance_verified
         AND e.provenance_digest=r.payload_digest
         AND r.idempotency_key=(CASE r.event_type
           WHEN 'order_confirmed' THEN 'backfill:confirmed:'
           WHEN 'refund' THEN 'backfill:refund:' ELSE '' END)||r.order_id::text))
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    # Freeze legacy order writes before the first classification snapshot. The
    # lock is bounded by lock_timeout and held through DML revoke/commit, so a
    # writer is either fully included in this transaction's backfill or the
    # cutover fails atomically before writing any ledger fact.
    op.execute("LOCK TABLE public.external_orders IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """WITH normalized AS (
          SELECT o.*,
            CASE WHEN o.amount IS NOT NULL
                   AND o.amount NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
                   AND abs(o.amount)<100000000000000::float8
                 THEN round(o.amount::numeric,6) END AS normalized_amount,
            CASE WHEN o.refund_amount IS NOT NULL
                   AND o.refund_amount NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
                   AND abs(o.refund_amount)<100000000000000::float8
                 THEN round(o.refund_amount::numeric,6) END AS normalized_refund
          FROM external_orders o
        )
        INSERT INTO external_order_ledger_recovery_markers(id,tenant_id,order_id,reason,snapshot_digest)
        SELECT gen_random_uuid(),tenant_id,id,
          CASE
            WHEN source_system IS NULL OR btrim(source_system)='' OR length(source_system)>50
              THEN 'legacy_source_identity_is_invalid'
            WHEN external_id IS NULL OR btrim(external_id)='' OR length(external_id)>100
              THEN 'legacy_order_identity_is_invalid'
            WHEN normalized_amount IS NULL OR normalized_amount<=0
              THEN 'legacy_original_amount_is_not_representable'
            WHEN normalized_refund IS NULL OR normalized_refund<0
                 OR (refund_amount>0 AND normalized_refund=0) OR normalized_refund>normalized_amount
              THEN 'legacy_refund_amount_is_not_representable'
            WHEN currency IS NULL OR currency !~ '^[A-Z]{3}$'
              THEN 'legacy_currency_is_invalid'
            WHEN status='cancelled' THEN 'legacy_cancel_has_no_immutable_provenance'
            ELSE 'legacy_value_state_is_inconsistent'
          END,
          encode(digest(convert_to(jsonb_build_array(
            id,source_system,external_id,amount,refund_amount,status,currency
          )::text,'UTF8'),'sha256'),'hex')
        FROM normalized
        WHERE source_system IS NULL OR btrim(source_system)='' OR length(source_system)>50
           OR external_id IS NULL OR btrim(external_id)='' OR length(external_id)>100
           OR normalized_amount IS NULL OR normalized_amount<=0
           OR normalized_refund IS NULL OR normalized_refund<0
           OR (refund_amount>0 AND normalized_refund=0) OR normalized_refund>normalized_amount
           OR currency IS NULL OR currency !~ '^[A-Z]{3}$'
           OR status IS NULL OR status='cancelled' OR status NOT IN ('paid','partially_refunded','refunded')
           OR (status='paid' AND normalized_refund<>0)
           OR (status='partially_refunded' AND (normalized_refund=0 OR normalized_refund=normalized_amount))
           OR (status='refunded' AND normalized_refund<>normalized_amount)
        ON CONFLICT (tenant_id,order_id) DO NOTHING"""
    )
    # Only unambiguous legacy rows become authoritative. Marked rows stay fail-closed.
    op.execute(
        """WITH candidates AS (
          SELECT o.*,gen_random_uuid() receipt_id,gen_random_uuid() event_id
          FROM external_orders o LEFT JOIN external_order_ledger_recovery_markers m
            ON m.tenant_id=o.tenant_id AND m.order_id=o.id WHERE m.id IS NULL
        ) INSERT INTO external_order_value_receipts(
          id,tenant_id,source_system,external_order_id,event_type,idempotency_key,payload_digest,order_id,event_id,
          result_original_amount,result_refunded_amount,result_cancelled_amount,result_net_amount,result_status)
        SELECT receipt_id,tenant_id,source_system,external_id,'order_confirmed','backfill:confirmed:'||id,
          encode(digest(convert_to(jsonb_build_array(id,amount,currency,order_time)::text,'UTF8'),'sha256'),'hex'),id,event_id,
          round(amount::numeric,6)::numeric(20,6),0,0,round(amount::numeric,6)::numeric(20,6),'paid'
          FROM candidates"""
    )
    op.execute(
        """INSERT INTO external_order_value_events(id,tenant_id,order_id,receipt_id,sequence_no,event_type,event_amount,
          currency,source_system,external_order_id,provenance_type,provenance_digest,provenance_verified,actor_type,occurred_at)
        SELECT r.event_id,r.tenant_id,r.order_id,r.id,1,'order_confirmed',r.result_original_amount,o.currency,r.source_system,
          r.external_order_id,'backfill',r.payload_digest,false,'migration',COALESCE(o.order_time,o.created_at,statement_timestamp())
        FROM external_order_value_receipts r JOIN external_orders o ON o.tenant_id=r.tenant_id AND o.id=r.order_id
        WHERE r.idempotency_key LIKE 'backfill:confirmed:%'"""
    )
    op.execute(
        """WITH candidates AS (
          SELECT o.*,gen_random_uuid() receipt_id,gen_random_uuid() event_id
          FROM external_orders o LEFT JOIN external_order_ledger_recovery_markers m
            ON m.tenant_id=o.tenant_id AND m.order_id=o.id WHERE m.id IS NULL AND o.refund_amount>0
        ) INSERT INTO external_order_value_receipts(
          id,tenant_id,source_system,external_order_id,event_type,idempotency_key,payload_digest,order_id,event_id,
          result_original_amount,result_refunded_amount,result_cancelled_amount,result_net_amount,result_status)
        SELECT receipt_id,tenant_id,source_system,external_id,'refund','backfill:refund:'||id,
          encode(digest(convert_to(jsonb_build_array(id,refund_amount,status)::text,'UTF8'),'sha256'),'hex'),id,event_id,
          round(amount::numeric,6)::numeric(20,6),round(refund_amount::numeric,6)::numeric(20,6),0,
          (round(amount::numeric,6)-round(refund_amount::numeric,6))::numeric(20,6),status FROM candidates"""
    )
    op.execute(
        """INSERT INTO external_order_value_events(id,tenant_id,order_id,receipt_id,sequence_no,event_type,event_amount,
          currency,source_system,external_order_id,provenance_type,provenance_digest,provenance_verified,actor_type,occurred_at)
        SELECT r.event_id,r.tenant_id,r.order_id,r.id,2,'refund',r.result_refunded_amount,o.currency,r.source_system,
          r.external_order_id,'backfill',r.payload_digest,false,'migration',COALESCE(o.updated_at,o.created_at,statement_timestamp())
        FROM external_order_value_receipts r JOIN external_orders o ON o.tenant_id=r.tenant_id AND o.id=r.order_id
        WHERE r.idempotency_key LIKE 'backfill:refund:%'"""
    )
    op.execute(
        """UPDATE external_orders o SET ledger_original_amount=round(o.amount::numeric,6)::numeric(20,6),
          ledger_refunded_amount=round(o.refund_amount::numeric,6)::numeric(20,6),ledger_cancelled_amount=0,
          ledger_net_amount=(round(o.amount::numeric,6)-round(o.refund_amount::numeric,6))::numeric(20,6),
          ledger_status=o.status
        WHERE NOT EXISTS(SELECT 1 FROM external_order_ledger_recovery_markers m WHERE m.tenant_id=o.tenant_id AND m.order_id=o.id)"""
    )
    op.create_foreign_key("fk_external_order_events_tenant_order_u8a", "external_order_value_events", "external_orders", ["tenant_id", "order_id"], ["tenant_id", "id"])
    op.create_foreign_key("fk_external_order_receipts_tenant_order_u8a", "external_order_value_receipts", "external_orders", ["tenant_id", "order_id"], ["tenant_id", "id"])
    op.create_foreign_key("fk_external_order_events_tenant_receipt_u8a", "external_order_value_events", "external_order_value_receipts", ["tenant_id", "receipt_id"], ["tenant_id", "id"])
    op.create_foreign_key("fk_external_order_receipts_tenant_event_u8a", "external_order_value_receipts", "external_order_value_events", ["tenant_id", "event_id"], ["tenant_id", "id"], deferrable=True, initially="DEFERRED")
    op.execute("ALTER TABLE external_orders ADD CONSTRAINT ck_external_orders_ledger_balance_u8a CHECK ((ledger_original_amount IS NULL AND ledger_refunded_amount IS NULL AND ledger_cancelled_amount IS NULL AND ledger_net_amount IS NULL AND ledger_status IS NULL) OR (ledger_original_amount>0 AND ledger_refunded_amount>=0 AND ledger_cancelled_amount>=0 AND ledger_net_amount>=0 AND ledger_refunded_amount+ledger_cancelled_amount+ledger_net_amount=ledger_original_amount AND ledger_status IN ('paid','partially_refunded','refunded','cancelled'))) NOT VALID")
    op.execute("ALTER TABLE external_orders VALIDATE CONSTRAINT ck_external_orders_ledger_balance_u8a")

    op.execute(
        """CREATE FUNCTION public.guard_external_order_ledger_facts() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
          RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='external order ledger facts are immutable';
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_external_order_ledger_facts() FROM PUBLIC")
    for table in ("external_order_value_receipts", "external_order_value_events", "external_order_ledger_recovery_markers"):
        op.execute(f"CREATE TRIGGER trg_guard_{table}_u8a BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.guard_external_order_ledger_facts()")

    op.execute(
        r"""CREATE FUNCTION public.record_external_order_value_event(
          requested_tenant_id uuid,requested_source_system text,requested_external_order_id text,requested_event_type text,
          requested_amount numeric,requested_currency text,requested_idempotency_key text,requested_payload_digest text,
          requested_provenance_digest text,requested_auth_session_id uuid,requested_occurred_at timestamptz,
          requested_order_time timestamptz DEFAULT NULL,requested_phone_hash text DEFAULT NULL,
          requested_product_name text DEFAULT NULL,requested_channel text DEFAULT NULL,requested_reason text DEFAULT NULL)
        RETURNS TABLE(receipt_id uuid,event_id uuid,order_id uuid,event_type text,event_amount numeric,
          original_amount numeric,refunded_amount numeric,cancelled_amount numeric,net_amount numeric,status text,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE prior external_order_value_receipts%ROWTYPE; target external_orders%ROWTYPE;
          new_receipt uuid:=gen_random_uuid();new_event uuid:=gen_random_uuid();next_sequence bigint;
          applied_amount numeric(20,6);new_refunded numeric(20,6);new_cancelled numeric(20,6);new_net numeric(20,6);new_status text;
          resolved_actor_id uuid;probed_session_tenant_id uuid;now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='external order ledger authority denied'; END IF;
          IF requested_source_system IS NULL OR btrim(requested_source_system)='' OR length(requested_source_system)>50
             OR requested_external_order_id IS NULL OR btrim(requested_external_order_id)='' OR length(requested_external_order_id)>100
             OR requested_event_type NOT IN ('order_confirmed','refund','cancel')
             OR requested_currency !~ '^[A-Z]{3}$' OR requested_idempotency_key IS NULL OR btrim(requested_idempotency_key)=''
             OR length(requested_idempotency_key)>128 OR requested_payload_digest !~ '^[0-9a-f]{64}$'
             OR requested_provenance_digest !~ '^[0-9a-f]{64}$'
             OR requested_auth_session_id IS NULL
             OR requested_occurred_at IS NULL OR (requested_event_type='cancel' AND requested_amount IS NOT NULL)
             OR (requested_event_type<>'cancel' AND (requested_amount IS NULL OR requested_amount='NaN'::numeric
                 OR requested_amount<=0 OR requested_amount>=100000000000000::numeric
                 OR requested_amount<>round(requested_amount,6))) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid external order value event'; END IF;
          IF NOT pg_try_advisory_xact_lock_shared(
            hashtextextended('auth-session:'||requested_auth_session_id::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='external order auth session is busy'; END IF;
          SELECT session.tenant_id INTO probed_session_tenant_id FROM auth_sessions session
            WHERE session.id=requested_auth_session_id;
          IF NOT FOUND OR probed_session_tenant_id IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='external order auth session is missing'; END IF;
          SELECT account.id INTO resolved_actor_id
          FROM auth_sessions session JOIN accounts account
            ON account.tenant_id=session.tenant_id AND account.id=session.account_id
          JOIN tenants tenant ON tenant.id=session.tenant_id
          WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
            AND session.revoked_at IS NULL AND session.expires_at>now_at
            AND NULLIF(btrim(session.current_refresh_jti),'') IS NOT NULL
            AND session.auth_version=account.auth_version AND account.is_active AND tenant.status='active';
          IF resolved_actor_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='external order auth session is not live'; END IF;
          IF NOT EXISTS(SELECT 1 FROM account_roles ar JOIN roles role
              ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN role_permissions rp ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
            JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=resolved_actor_id
              AND role.name IN ('admin','operator') AND permission.code='order:manage') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='external order manage permission denied'; END IF;
          IF NOT pg_try_advisory_xact_lock(hashtextextended('external-order:'||requested_tenant_id||':'||requested_source_system||':'||requested_external_order_id,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='external order value identity is busy'; END IF;
          SELECT * INTO prior FROM external_order_value_receipts r WHERE r.tenant_id=requested_tenant_id
            AND r.source_system=requested_source_system AND r.idempotency_key=requested_idempotency_key;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest OR prior.external_order_id<>requested_external_order_id OR prior.event_type<>requested_event_type THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='external order idempotency payload conflicts'; END IF;
            RETURN QUERY SELECT prior.id,prior.event_id,prior.order_id,prior.event_type::text,e.event_amount::numeric,
              prior.result_original_amount::numeric,prior.result_refunded_amount::numeric,
              prior.result_cancelled_amount::numeric,prior.result_net_amount::numeric,prior.result_status::text,true
              FROM external_order_value_events e WHERE e.id=prior.event_id; RETURN;
          END IF;
          SELECT * INTO target FROM external_orders o WHERE o.tenant_id=requested_tenant_id
            AND o.source_system=requested_source_system AND o.external_id=requested_external_order_id FOR UPDATE;
          IF requested_event_type='order_confirmed' THEN
            IF FOUND THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='external order identity already exists'; END IF;
            INSERT INTO external_orders(id,tenant_id,external_id,amount,phone_hash,product_name,order_time,matched,channel,source_system,
              status,refund_amount,currency,ledger_original_amount,ledger_refunded_amount,ledger_cancelled_amount,ledger_net_amount,ledger_status)
            VALUES(gen_random_uuid(),requested_tenant_id,requested_external_order_id,requested_amount::float8,requested_phone_hash,
              requested_product_name,requested_order_time,false,requested_channel,requested_source_system,'paid',0,requested_currency,
              requested_amount,0,0,requested_amount,'paid') RETURNING * INTO target;
            applied_amount:=requested_amount;new_refunded:=0;new_cancelled:=0;new_net:=requested_amount;new_status:='paid';next_sequence:=1;
          ELSE
            IF NOT FOUND OR target.ledger_original_amount IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='external order confirmation is missing or quarantined'; END IF;
            IF target.currency<>requested_currency THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='external order currency mismatch'; END IF;
            IF target.ledger_status NOT IN ('paid','partially_refunded') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='external order is terminal'; END IF;
            SELECT COALESCE(max(e.sequence_no),0)+1 INTO next_sequence FROM external_order_value_events e WHERE e.tenant_id=requested_tenant_id AND e.order_id=target.id;
            IF requested_event_type='refund' THEN
              IF requested_amount>target.ledger_net_amount THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='refund exceeds remaining order value'; END IF;
              applied_amount:=requested_amount;new_refunded:=target.ledger_refunded_amount+requested_amount;new_cancelled:=target.ledger_cancelled_amount;
              new_net:=target.ledger_net_amount-requested_amount;new_status:=CASE WHEN new_net=0 THEN 'refunded' ELSE 'partially_refunded' END;
            ELSE
              applied_amount:=target.ledger_net_amount;new_refunded:=target.ledger_refunded_amount;new_cancelled:=target.ledger_cancelled_amount+target.ledger_net_amount;
              new_net:=0;new_status:='cancelled';
            END IF;
            UPDATE external_orders SET ledger_refunded_amount=new_refunded,ledger_cancelled_amount=new_cancelled,
              ledger_net_amount=new_net,ledger_status=new_status,refund_amount=new_refunded::float8,status=new_status,updated_at=statement_timestamp()
              WHERE id=target.id;
            UPDATE gmv_attributions SET amount=new_net::float8,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND external_order_id=target.id;
          END IF;
          INSERT INTO external_order_value_receipts(id,tenant_id,source_system,external_order_id,event_type,idempotency_key,payload_digest,
            order_id,event_id,result_original_amount,result_refunded_amount,result_cancelled_amount,result_net_amount,result_status)
          VALUES(new_receipt,requested_tenant_id,requested_source_system,requested_external_order_id,requested_event_type,
            requested_idempotency_key,requested_payload_digest,target.id,new_event,target.ledger_original_amount,new_refunded,new_cancelled,new_net,new_status);
          INSERT INTO external_order_value_events(id,tenant_id,order_id,receipt_id,sequence_no,event_type,event_amount,currency,source_system,
            external_order_id,provenance_type,provenance_digest,provenance_verified,actor_type,actor_id,reason,occurred_at)
          VALUES(new_event,requested_tenant_id,target.id,new_receipt,next_sequence,requested_event_type,applied_amount,requested_currency,
            requested_source_system,requested_external_order_id,'manual_import',requested_provenance_digest,
            false,'account',resolved_actor_id,requested_reason,requested_occurred_at);
          RETURN QUERY SELECT new_receipt,new_event,target.id,requested_event_type,applied_amount,target.ledger_original_amount,
            new_refunded,new_cancelled,new_net,new_status,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC")
    op.execute(
        f"DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION} TO yimatong_app; "
        "GRANT SELECT ON external_orders,external_order_value_receipts,external_order_value_events TO yimatong_app; "
        "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON external_orders,external_order_value_receipts,external_order_value_events,external_order_ledger_recovery_markers FROM yimatong_app; "
        "END IF; END $do$"
    )
    op.execute("DROP TRIGGER trg_coordinate_external_order_index_build_u8a ON public.external_orders")
    op.execute("DROP FUNCTION public.coordinate_external_order_index_build_u8a()")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    blocking_facts = op.get_bind().execute(sa.text(_DOWNGRADE_BLOCKING_FACTS)).scalar_one()
    if blocking_facts:
        raise RuntimeError("u8a2 downgrade blocked: immutable value facts or recovery markers exist")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}")
    for table in ("external_order_ledger_recovery_markers", "external_order_value_events", "external_order_value_receipts"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_guard_{table}_u8a ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.guard_external_order_ledger_facts()")
    op.drop_constraint("ck_external_orders_ledger_balance_u8a", "external_orders", type_="check")
    op.drop_constraint("fk_external_order_receipts_tenant_event_u8a", "external_order_value_receipts", type_="foreignkey")
    op.drop_constraint("fk_external_order_events_tenant_receipt_u8a", "external_order_value_events", type_="foreignkey")
    op.drop_constraint("fk_external_order_receipts_tenant_order_u8a", "external_order_value_receipts", type_="foreignkey")
    op.drop_constraint("fk_external_order_events_tenant_order_u8a", "external_order_value_events", type_="foreignkey")
    op.execute("DELETE FROM external_order_value_events WHERE provenance_type='backfill'")
    op.execute("DELETE FROM external_order_value_receipts WHERE idempotency_key LIKE 'backfill:%'")
    op.execute("UPDATE external_orders SET ledger_original_amount=NULL,ledger_refunded_amount=NULL,ledger_cancelled_amount=NULL,ledger_net_amount=NULL,ledger_status=NULL")
    op.execute(
        """CREATE FUNCTION public.coordinate_external_order_index_build_u8a() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('u8a:external_orders:index-build',0));
          RETURN NULL;
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.coordinate_external_order_index_build_u8a() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_coordinate_external_order_index_build_u8a "
        "BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.external_orders "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.coordinate_external_order_index_build_u8a()"
    )
    op.execute(
        "DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        "REVOKE ALL PRIVILEGES ON public.external_orders,public.external_order_value_receipts,"
        "public.external_order_value_events,public.external_order_ledger_recovery_markers FROM yimatong_app; "
        "GRANT SELECT,INSERT,UPDATE,DELETE ON public.external_orders TO yimatong_app; "
        "END IF; END $do$"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON public.external_orders,public.external_order_value_receipts,"
        "public.external_order_value_events,public.external_order_ledger_recovery_markers FROM PUBLIC"
    )
