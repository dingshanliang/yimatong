"""bind verified WeCom contact events to immutable database authority

Revision ID: u6l0c1d2e3f4
Revises: u6k1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

from alembic import context, op

revision: str = "u6l0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u6k1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"


def _role_exists(role: str) -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar())


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("WeCom authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.wecom_callback_receipts")).scalar_one():
        raise RuntimeError("cannot downgrade while immutable verified WeCom callback receipts exist")
    if (
        _destination_is_below("u6b5c6d7e8f9")
        and bind.execute(
            sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
        ).scalar_one()
    ):
        raise RuntimeError("u6l0 downgrade blocked: bound benefit claims are immutable facts")
    if (
        _destination_is_below("u7c0e1f2a3b4")
        and bind.execute(sa.text("SELECT count(*) FROM public.risk_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l0 downgrade blocked: risk action receipts are immutable facts")
    if (
        _destination_is_below("u7b0c1d2e3f4")
        and bind.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM public.diversion_observations)+"
                "(SELECT count(*) FROM public.diversion_action_receipts)+"
                "(SELECT count(*) FROM public.diversion_evidence)+"
                "(SELECT count(*) FROM public.diversion_investigation_history)"
            )
        ).scalar_one()
    ):
        raise RuntimeError("u6l0 downgrade blocked: immutable diversion investigation facts exist")
    if (
        _destination_is_below("u7a0c1d2e3f4")
        and bind.execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l0 downgrade blocked: channel action receipts are immutable facts")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_table(
        "wecom_callback_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("event_identity_digest", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("external_userid", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=True),
        sa.Column("state", sa.String(128), nullable=True),
        sa.Column("unionid", sa.String(120), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_wecom_callback_receipts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "connector_id", "event_identity_digest", name="uq_wecom_callback_receipts_event"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_wecom_callback_receipts_tenant_connector",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(event_identity_digest)=64", name="ck_wecom_callback_receipts_event_digest"),
        sa.CheckConstraint("length(payload_digest)=64", name="ck_wecom_callback_receipts_payload_digest"),
        sa.CheckConstraint("event_sequence>=0", name="ck_wecom_callback_receipts_event_sequence"),
    )
    op.create_index("ix_wecom_callback_receipts_tenant_id", "wecom_callback_receipts", ["tenant_id"])
    op.create_index("ix_wecom_callback_receipts_connector_id", "wecom_callback_receipts", ["connector_id"])
    op.create_index(
        "ix_wecom_callback_receipts_contact_order",
        "wecom_callback_receipts",
        ["tenant_id", "connector_id", "external_userid", "event_time", "event_sequence"],
    )
    op.execute("ALTER TABLE public.wecom_callback_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.wecom_callback_receipts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.wecom_callback_receipts "
        "USING (tenant_id=public.current_tenant_id()) WITH CHECK (tenant_id=public.current_tenant_id())"
    )
    op.execute("ALTER TABLE public.wecom_external_contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.wecom_external_contacts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.wecom_external_contacts")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.wecom_external_contacts "
        "USING (tenant_id=public.current_tenant_id()) WITH CHECK (tenant_id=public.current_tenant_id())"
    )
    op.execute(r"""
      CREATE FUNCTION public.guard_wecom_callback_receipts()
      RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      BEGIN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='verified WeCom callback receipts are immutable'; END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.guard_wecom_callback_receipts() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_wecom_callback_receipts BEFORE UPDATE OR DELETE "
        "ON public.wecom_callback_receipts FOR EACH ROW EXECUTE FUNCTION public.guard_wecom_callback_receipts()"
    )
    op.execute(r"""
      CREATE FUNCTION public.apply_verified_wecom_contact_event(
        requested_tenant_id uuid,requested_receipt_id uuid,requested_connector_id uuid,
        requested_change_type text,requested_external_userid text,requested_user_id text,
        requested_state text,requested_unionid text,requested_event_time timestamptz,
        requested_event_sequence bigint,requested_payload_digest text
      ) RETURNS TABLE(receipt_id uuid,outcome text,contact_id uuid,current_status text,
        verification_source text,welcome_code_pending boolean,replayed boolean,confirmed boolean,
        event_time timestamptz,event_sequence bigint)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE identity_digest text;DECLARE event_rank integer;DECLARE prior wecom_callback_receipts%ROWTYPE;
      DECLARE newest wecom_external_contacts%ROWTYPE;DECLARE target wecom_external_contacts%ROWTYPE;
      DECLARE way wecom_contact_ways%ROWTYPE;DECLARE result_value jsonb;DECLARE is_delete boolean;
      DECLARE incoming_order text;DECLARE current_order text;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
      BEGIN
        IF session_user<>'yimatong_callback' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
          OR requested_receipt_id IS NULL OR requested_connector_id IS NULL
          OR requested_change_type NOT IN ('add_external_contact','add_half_external_contact',
            'del_external_contact','del_follow_user')
          OR NULLIF(trim(requested_external_userid),'') IS NULL OR length(trim(requested_external_userid))>120
          OR NULLIF(trim(requested_user_id),'') IS NULL OR length(trim(requested_user_id))>120
          OR (requested_change_type IN ('add_external_contact','add_half_external_contact')
            AND NULLIF(trim(requested_state),'') IS NULL) OR length(trim(COALESCE(requested_state,'')))>128
          OR length(COALESCE(requested_unionid,''))>120 OR requested_event_time IS NULL
          OR requested_event_sequence<0 OR requested_payload_digest !~ '^[0-9a-f]{64}$' THEN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='verified WeCom event authority invalid'; END IF;
        event_rank:=CASE requested_change_type WHEN 'add_half_external_contact' THEN 0
          WHEN 'add_external_contact' THEN 1 ELSE 2 END;
        is_delete:=requested_change_type IN ('del_external_contact','del_follow_user');
        identity_digest:=encode(digest(convert_to(jsonb_build_array(1,requested_tenant_id,
          requested_connector_id,requested_change_type,trim(requested_external_userid),
          NULLIF(trim(requested_user_id),''),NULLIF(trim(requested_state),''),
          NULLIF(trim(requested_unionid),''),requested_event_time,requested_event_sequence)::text,'UTF8'),
          'sha256'),'hex');
        IF NOT pg_try_advisory_xact_lock(hashtextextended('wecom-contact:'||requested_tenant_id::text||':'||
          requested_connector_id::text||':'||trim(requested_user_id)||':'||trim(requested_external_userid),0)) THEN
          RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='verified WeCom event authority is busy'; END IF;
        PERFORM id FROM connectors c WHERE c.tenant_id=requested_tenant_id AND c.id=requested_connector_id
          AND c.connector_type='wecom_customer_contact' AND c.enabled FOR SHARE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='verified WeCom connector unavailable'; END IF;
        SELECT * INTO way FROM wecom_contact_ways w WHERE w.tenant_id=requested_tenant_id
          AND w.connector_id=requested_connector_id AND w.status='active'
          AND jsonb_typeof(w.user_ids::jsonb)='array'
          AND w.user_ids::jsonb @> jsonb_build_array(trim(requested_user_id))
          AND (w.state=trim(requested_state) OR (is_delete AND NULLIF(trim(requested_state),'') IS NULL AND EXISTS (
            SELECT 1 FROM wecom_external_contacts c WHERE c.tenant_id=requested_tenant_id
              AND c.connector_id=requested_connector_id AND c.contact_way_id=w.id
              AND c.user_id=trim(requested_user_id) AND c.external_userid=trim(requested_external_userid))))
          FOR SHARE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='verified WeCom member unavailable'; END IF;
        SELECT * INTO prior FROM wecom_callback_receipts r WHERE r.tenant_id=requested_tenant_id
          AND r.connector_id=requested_connector_id AND r.event_identity_digest=identity_digest;
        IF FOUND THEN
          IF prior.payload_digest<>requested_payload_digest THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='conflicting verified WeCom event replay'; END IF;
          receipt_id:=prior.id;outcome:=prior.outcome;contact_id:=prior.contact_id;
          current_status:=prior.result->>'current_status';verification_source:=prior.result->>'verification_source';
          welcome_code_pending:=COALESCE((prior.result->>'welcome_code_pending')::boolean,false);
          confirmed:=COALESCE((prior.result->>'confirmed')::boolean,false);replayed:=true;
          event_time:=prior.event_time;event_sequence:=prior.event_sequence;RETURN NEXT;RETURN;
        END IF;
        PERFORM id FROM wecom_external_contacts c WHERE c.tenant_id=requested_tenant_id
          AND c.connector_id=requested_connector_id AND c.user_id=trim(requested_user_id)
          AND c.external_userid=trim(requested_external_userid)
          ORDER BY c.id::text FOR UPDATE;
        SELECT * INTO newest FROM wecom_external_contacts c WHERE c.tenant_id=requested_tenant_id
          AND c.connector_id=requested_connector_id AND c.user_id=trim(requested_user_id)
          AND c.external_userid=trim(requested_external_userid)
          ORDER BY c.event_time DESC,c.event_sequence DESC,
            CASE c.change_type WHEN 'add_half_external_contact' THEN 0
              WHEN 'add_external_contact' THEN 1 ELSE 2 END DESC,
            COALESCE(c.event_fingerprint,'') DESC LIMIT 1;
        incoming_order:=to_char(requested_event_time AT TIME ZONE 'UTC','YYYYMMDDHH24MISS.US')||':'||
          lpad(requested_event_sequence::text,20,'0')||':'||event_rank::text||':'||identity_digest;
        IF FOUND THEN current_order:=to_char(newest.event_time AT TIME ZONE 'UTC','YYYYMMDDHH24MISS.US')||':'||
          lpad(newest.event_sequence::text,20,'0')||':'||
          (CASE newest.change_type WHEN 'add_half_external_contact' THEN 0
            WHEN 'add_external_contact' THEN 1 ELSE 2 END)::text||':'||
          COALESCE(newest.event_fingerprint,''); END IF;
        IF newest.id IS NOT NULL AND incoming_order<=current_order THEN
          outcome:='ignored_stale';contact_id:=newest.id;current_status:=newest.status;
          verification_source:=newest.verification_source;welcome_code_pending:=newest.welcome_code_pending;
          confirmed:=newest.status='active' AND newest.verification_source='confirmed_callback'
            AND NOT newest.welcome_code_pending;replayed:=false;
        ELSE
          SELECT * INTO target FROM wecom_external_contacts c WHERE c.tenant_id=requested_tenant_id
            AND c.connector_id=requested_connector_id AND c.user_id=trim(requested_user_id)
            AND c.external_userid=trim(requested_external_userid)
            ORDER BY (c.state IS NOT DISTINCT FROM NULLIF(trim(requested_state),'')) DESC,c.id::text LIMIT 1;
          IF target.id IS NULL THEN
            target.id:=gen_random_uuid();
            INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,external_userid,user_id,state,status,
              raw_event,event_time,event_sequence,welcome_code_pending,created_at,updated_at)
            VALUES(target.id,requested_tenant_id,requested_connector_id,trim(requested_external_userid),
              NULLIF(trim(requested_user_id),''),NULLIF(trim(requested_state),''),'deleted','{}',
              requested_event_time,requested_event_sequence,false,now_at,now_at);
          END IF;
          UPDATE wecom_external_contacts c SET status='deleted',deleted_at=requested_event_time,
            welcome_code_pending=false,change_type=requested_change_type,event_time=requested_event_time,
            event_sequence=requested_event_sequence,event_fingerprint=identity_digest,raw_event='{}',updated_at=now_at
          WHERE c.tenant_id=requested_tenant_id AND c.connector_id=requested_connector_id
            AND c.user_id=trim(requested_user_id) AND c.external_userid=trim(requested_external_userid);
          UPDATE wecom_external_contacts c SET contact_way_id=way.id,campaign_id=way.campaign_id,
            benefit_id=way.benefit_id,scan_token_hash=way.scan_token_hash,user_id=trim(requested_user_id),
            state=CASE WHEN is_delete THEN c.state ELSE NULLIF(trim(requested_state),'') END,
            unionid=COALESCE(NULLIF(trim(requested_unionid),''),c.unionid),
            status=CASE WHEN is_delete THEN 'deleted' ELSE 'active' END,
            verification_source=CASE WHEN is_delete THEN 'termination_callback'
              WHEN requested_change_type='add_half_external_contact' THEN 'pending_callback'
              ELSE 'confirmed_callback' END,
            change_type=requested_change_type,event_fingerprint=identity_digest,event_time=requested_event_time,
            event_sequence=requested_event_sequence,welcome_code_pending=requested_change_type='add_half_external_contact',
            added_at=CASE WHEN requested_change_type='add_external_contact' THEN requested_event_time ELSE NULL END,
            deleted_at=CASE WHEN is_delete THEN requested_event_time ELSE NULL END,raw_event='{}',updated_at=now_at
          WHERE c.tenant_id=requested_tenant_id AND c.id=target.id RETURNING * INTO target;
          outcome:='recorded';contact_id:=target.id;current_status:=target.status;
          verification_source:=target.verification_source;welcome_code_pending:=target.welcome_code_pending;
          confirmed:=target.status='active' AND target.verification_source='confirmed_callback'
            AND NOT target.welcome_code_pending;replayed:=false;
        END IF;
        result_value:=jsonb_build_object('outcome',outcome,'contact_id',contact_id,
          'current_status',current_status,'verification_source',verification_source,
          'welcome_code_pending',welcome_code_pending,'confirmed',confirmed,
          'event_time',requested_event_time,'event_sequence',requested_event_sequence);
        INSERT INTO wecom_callback_receipts(id,tenant_id,connector_id,event_identity_digest,payload_digest,
          change_type,external_userid,user_id,state,unionid,event_time,event_sequence,outcome,contact_id,result,recorded_at)
        VALUES(requested_receipt_id,requested_tenant_id,requested_connector_id,identity_digest,
          requested_payload_digest,requested_change_type,trim(requested_external_userid),
          trim(requested_user_id),NULLIF(trim(requested_state),''),NULLIF(trim(requested_unionid),''),
          requested_event_time,requested_event_sequence,outcome,contact_id,result_value,now_at);
        receipt_id:=requested_receipt_id;event_time:=requested_event_time;event_sequence:=requested_event_sequence;
        RETURN NEXT;
      EXCEPTION WHEN lock_not_available THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='verified WeCom event authority is busy';
      END $fn$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC")
    for role in ("yimatong_app", "yimatong_callback"):
        if _role_exists(role):
            op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM {role}")
    if _role_exists("yimatong_callback"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION} TO yimatong_callback")
    for table in ("wecom_callback_receipts", "wecom_external_contacts"):
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        for role in ("yimatong_app", "yimatong_callback"):
            if _role_exists(role):
                op.execute(f"REVOKE ALL ON public.{table} FROM {role}")
    if _role_exists("yimatong_app"):
        op.execute("GRANT SELECT ON public.wecom_callback_receipts,public.wecom_external_contacts TO yimatong_app")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    _downgrade_preflight()
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_wecom_callback_receipts ON public.wecom_callback_receipts")
    op.execute("DROP FUNCTION IF EXISTS public.guard_wecom_callback_receipts()")
    op.drop_index("ix_wecom_callback_receipts_contact_order", table_name="wecom_callback_receipts")
    op.drop_index("ix_wecom_callback_receipts_connector_id", table_name="wecom_callback_receipts")
    op.drop_index("ix_wecom_callback_receipts_tenant_id", table_name="wecom_callback_receipts")
    op.drop_table("wecom_callback_receipts")
    op.execute("ALTER TABLE public.wecom_external_contacts NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.wecom_external_contacts")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.wecom_external_contacts "
        "USING (tenant_id=public.current_tenant_id() OR public.current_tenant_id() IS NULL) "
        "WITH CHECK (tenant_id=public.current_tenant_id() OR public.current_tenant_id() IS NULL)"
    )
    if _role_exists("yimatong_app"):
        op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.wecom_external_contacts TO yimatong_app")
