"""bind verified WeCom contact authority to the serving member

Revision ID: u6l4a5b6c7d8
Revises: u6l3f4a5b6c7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

from alembic import context, op

revision: str = "u6l4a5b6c7d8"
down_revision: str | Sequence[str] | None = "u6l3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"
_LEGACY_FUNCTION = (
    "apply_verified_wecom_contact_event_u6l3(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"
)
_MEMBER_KEY = "uq_wecom_external_contacts_member_source_u6l"
_LEGACY_KEY = "uq_wecom_external_contacts_source"
_LEGACY_RESTORE_INDEX = "uq_wecom_external_contacts_source_u6l_restore"
_MEMBER_RESTORE_INDEX = "uq_wecom_external_contacts_member_source_u6l_restore"
_LEGACY_RESTORE_EXPECTED = (
    "CREATE UNIQUE INDEX uq_wecom_external_contacts_source_u6l_restore "
    "ON public.wecom_external_contacts USING btree (tenant_id, connector_id, external_userid, state)"
)
_MEMBER_RESTORE_EXPECTED = (
    "CREATE UNIQUE INDEX uq_wecom_external_contacts_member_source_u6l_restore "
    "ON public.wecom_external_contacts USING btree (tenant_id, connector_id, user_id, external_userid)"
)


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("WeCom member authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downstream_fact_preflight() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.wecom_callback_receipts")).scalar_one():
        raise RuntimeError("cannot downgrade while immutable verified WeCom callback receipts exist")
    if (
        _destination_is_below("u6k1d2e3f4a5")
        and bind.execute(sa.text("SELECT count(*) FROM public.campaign_delivery_callback_attempts")).scalar_one()
    ):
        raise RuntimeError("u6l4 downgrade blocked: campaign delivery callback attempts are immutable facts")
    if (
        _destination_is_below("u6b5c6d7e8f9")
        and bind.execute(
            sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
        ).scalar_one()
    ):
        raise RuntimeError("u6l4 downgrade blocked: bound benefit claims are immutable facts")
    if (
        _destination_is_below("u7c0e1f2a3b4")
        and bind.execute(sa.text("SELECT count(*) FROM public.risk_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l4 downgrade blocked: risk action receipts are immutable facts")
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
        raise RuntimeError("u6l4 downgrade blocked: immutable diversion investigation facts exist")
    if (
        _destination_is_below("u7a0c1d2e3f4")
        and bind.execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l4 downgrade blocked: channel action receipts are immutable facts")


def _legacy_index_facts() -> tuple[bool, bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": _LEGACY_RESTORE_INDEX},
        )
        .one_or_none()
    )
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _prepare_legacy_index() -> None:
    valid, unique, definition = _legacy_index_facts()
    if valid and unique and definition == _LEGACY_RESTORE_EXPECTED:
        return
    if definition is not None and (not unique or definition != _LEGACY_RESTORE_EXPECTED):
        raise RuntimeError(f"refusing unexpected WeCom downgrade index public.{_LEGACY_RESTORE_INDEX}")
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        op.execute("SET statement_timeout='5s'")
        try:
            if definition is not None:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_LEGACY_RESTORE_INDEX}")
            try:
                op.execute(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {_LEGACY_RESTORE_INDEX} "
                    "ON public.wecom_external_contacts (tenant_id,connector_id,external_userid,state)"
                )
            except BaseException:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_LEGACY_RESTORE_INDEX}")
                raise
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")
    valid, unique, definition = _legacy_index_facts()
    if not valid or not unique or definition != _LEGACY_RESTORE_EXPECTED:
        raise RuntimeError(f"WeCom downgrade index public.{_LEGACY_RESTORE_INDEX} is not exact and valid")


def _prepare_member_restore_index() -> None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": _MEMBER_RESTORE_INDEX},
        )
        .one_or_none()
    )
    valid, unique, definition = (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))
    if valid and unique and definition == _MEMBER_RESTORE_EXPECTED:
        return
    if definition is not None and (not unique or definition != _MEMBER_RESTORE_EXPECTED):
        raise RuntimeError(f"refusing unexpected WeCom downgrade index public.{_MEMBER_RESTORE_INDEX}")
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout='5s'")
        op.execute("SET statement_timeout='5s'")
        try:
            if definition is not None:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_MEMBER_RESTORE_INDEX}")
            try:
                op.execute(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {_MEMBER_RESTORE_INDEX} "
                    "ON public.wecom_external_contacts (tenant_id,connector_id,user_id,external_userid)"
                )
            except BaseException:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_MEMBER_RESTORE_INDEX}")
                raise
        finally:
            op.execute("SET statement_timeout=DEFAULT")
            op.execute("SET lock_timeout=DEFAULT")


def _preflight() -> None:
    bind = op.get_bind()
    unresolved_markers = bind.execute(
        sa.text("SELECT count(*) FROM public.wecom_member_recovery_markers WHERE resolution_state='unresolved'")
    ).scalar_one()
    invalid_receipts = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.wecom_callback_receipts r "
            "LEFT JOIN public.wecom_external_contacts c ON c.tenant_id=r.tenant_id AND c.id=r.contact_id "
            "LEFT JOIN public.wecom_contact_ways w ON w.tenant_id=r.tenant_id AND w.connector_id=r.connector_id "
            "AND (w.state=r.state OR (r.change_type IN ('del_external_contact','del_follow_user') "
            "AND r.state IS NULL AND w.id=c.contact_way_id)) "
            "AND w.status='active' AND jsonb_typeof(w.user_ids::jsonb)='array' "
            "AND w.user_ids::jsonb @> jsonb_build_array(r.user_id) "
            "WHERE r.user_id IS NULL OR NULLIF(trim(r.user_id),'') IS NULL OR w.id IS NULL"
        )
    ).scalar_one()
    ambiguous_projection = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.wecom_external_contacts c "
            "WHERE c.verification_source IN ('confirmed_callback','pending_callback','termination_callback') "
            "AND (c.user_id IS NULL OR NULLIF(trim(c.user_id),'') IS NULL OR NOT EXISTS ("
            "SELECT 1 FROM public.wecom_contact_ways w WHERE w.tenant_id=c.tenant_id "
            "AND w.connector_id=c.connector_id AND w.state=c.state AND w.status='active' "
            "AND jsonb_typeof(w.user_ids::jsonb)='array' AND w.user_ids::jsonb @> jsonb_build_array(c.user_id)))"
        )
    ).scalar_one()
    if unresolved_markers or invalid_receipts or ambiguous_projection:
        raise RuntimeError("cannot bind verified WeCom facts to an exact serving member")


def _install_function() -> None:
    op.execute(r"""
      CREATE OR REPLACE FUNCTION public.apply_verified_wecom_contact_event(
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
        identity_digest:=encode(digest(convert_to(jsonb_build_array(2,requested_tenant_id,
          requested_connector_id,requested_change_type,trim(requested_user_id),trim(requested_external_userid),
          trim(requested_state),NULLIF(trim(requested_unionid),''),requested_event_time,requested_event_sequence)::text,
          'UTF8'),'sha256'),'hex');
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
          AND c.external_userid=trim(requested_external_userid) ORDER BY c.id::text FOR UPDATE;
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
            ORDER BY (c.state IS NOT DISTINCT FROM trim(requested_state)) DESC,c.id::text LIMIT 1;
          IF target.id IS NULL THEN
            target.id:=gen_random_uuid();
            INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,external_userid,user_id,state,status,
              raw_event,event_time,event_sequence,welcome_code_pending,created_at,updated_at)
            VALUES(target.id,requested_tenant_id,requested_connector_id,trim(requested_external_userid),
              trim(requested_user_id),trim(requested_state),'deleted','{}',
              requested_event_time,requested_event_sequence,false,now_at,now_at);
          END IF;
          UPDATE wecom_external_contacts c SET status='deleted',deleted_at=requested_event_time,
            welcome_code_pending=false,change_type=requested_change_type,event_time=requested_event_time,
            event_sequence=requested_event_sequence,event_fingerprint=identity_digest,raw_event='{}',updated_at=now_at
          WHERE c.tenant_id=requested_tenant_id AND c.connector_id=requested_connector_id
            AND c.user_id=trim(requested_user_id) AND c.external_userid=trim(requested_external_userid);
          UPDATE wecom_external_contacts c SET contact_way_id=way.id,campaign_id=way.campaign_id,
            benefit_id=way.benefit_id,scan_token_hash=way.scan_token_hash,user_id=trim(requested_user_id),
            state=CASE WHEN is_delete THEN c.state ELSE trim(requested_state) END,
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


def _restore_legacy_function() -> None:
    op.execute(r"""
      DO $body$ DECLARE definition text;
      BEGIN
        definition:=pg_get_functiondef(
          'public.apply_verified_wecom_contact_event_u6l3(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)'
          ::regprocedure);
        definition:=replace(definition,'public.apply_verified_wecom_contact_event_u6l3(',
          'public.apply_verified_wecom_contact_event(');
        IF definition NOT LIKE '%public.apply_verified_wecom_contact_event(%' THEN
          RAISE EXCEPTION 'could not restore the exact pre-member WeCom authority';
        END IF;
        EXECUTE definition;
      END $body$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC")
    for role in ("yimatong_app", "yimatong_callback"):
        if op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
            op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM {role}")
    if op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname='yimatong_callback'")).scalar():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION} TO yimatong_callback")


def upgrade() -> None:
    _preflight()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.wecom_external_contacts DROP CONSTRAINT uq_wecom_external_contacts_source")
    op.execute(
        f"ALTER TABLE public.wecom_external_contacts ADD CONSTRAINT {_MEMBER_KEY} UNIQUE USING INDEX {_MEMBER_KEY}"
    )
    op.execute(
        "ALTER TABLE public.wecom_callback_receipts ADD CONSTRAINT ck_wecom_callback_receipts_user_id_nn_u6l "
        "CHECK (NULLIF(trim(user_id),'') IS NOT NULL) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.wecom_callback_receipts VALIDATE CONSTRAINT ck_wecom_callback_receipts_user_id_nn_u6l"
    )
    op.execute("ALTER TABLE public.wecom_callback_receipts ALTER COLUMN user_id SET NOT NULL")
    op.execute("ALTER TABLE public.wecom_callback_receipts DROP CONSTRAINT ck_wecom_callback_receipts_user_id_nn_u6l")
    _install_function()


def downgrade() -> None:
    _downstream_fact_preflight()
    _prepare_legacy_index()
    try:
        _prepare_member_restore_index()
    except BaseException:
        with op.get_context().autocommit_block():
            op.execute("SET lock_timeout='5s'")
            op.execute("SET statement_timeout='5s'")
            try:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_LEGACY_RESTORE_INDEX}")
            finally:
                op.execute("SET statement_timeout=DEFAULT")
                op.execute("SET lock_timeout=DEFAULT")
        raise
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("ALTER TABLE public.wecom_callback_receipts ALTER COLUMN user_id DROP NOT NULL")
    op.execute(f"ALTER TABLE public.wecom_external_contacts DROP CONSTRAINT {_MEMBER_KEY}")
    op.execute(f"ALTER INDEX public.{_MEMBER_RESTORE_INDEX} RENAME TO {_MEMBER_KEY}")
    op.execute(
        f"ALTER TABLE public.wecom_external_contacts ADD CONSTRAINT {_LEGACY_KEY} "
        f"UNIQUE USING INDEX {_LEGACY_RESTORE_INDEX}"
    )
    _restore_legacy_function()
