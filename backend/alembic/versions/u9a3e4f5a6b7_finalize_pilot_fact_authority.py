"""finalize pilot fact authority

Revision ID: u9a3e4f5a6b7
Revises: u9a2d3e4f5a6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u9a3e4f5a6b7"
down_revision: str | Sequence[str] | None = "u9a2d3e4f5a6"
branch_labels = None
depends_on = None

_UPDATE = "update_pending_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text)"
_COMPLETE = "complete_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text,text)"
_NOTE = "append_retrospective_note_authority(uuid,uuid,uuid,uuid,bigint,text,text,text)"
_GENERATE = "materialize_due_retrospective(uuid,uuid,uuid,integer,timestamptz,timestamptz,date,jsonb,text,uuid)"


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("pilot authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downstream_fact_preflight() -> None:
    """Reject unsafe deep targets at U09 head before any catalog drift commits."""

    bind = op.get_bind()
    if _destination_is_below("u9a0b1c2d3e4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM pilot_milestones WHERE authority_version=1)+"
            "(SELECT count(*) FROM pilot_milestone_corrections WHERE authority_version=1)+"
            "(SELECT count(*) FROM retrospectives WHERE authority_version=1)+"
            "(SELECT count(*) FROM pilot_authority_receipts WHERE actor_type<>'legacy')"
        )
    ).scalar_one():
        raise RuntimeError("u9a3 downgrade blocked: immutable pilot authority facts exist")
    parent_revision = ScriptDirectory.from_config(context.config).get_revision("u8d4c5d6e7f8")
    if (
        parent_revision is None
        or not hasattr(parent_revision.module, "_downstream_fact_preflight")
        or not hasattr(parent_revision.module, "_EXTERNAL_ORDER_BLOCKING_FACTS")
    ):
        raise RuntimeError("u9a3 downgrade blocked: parent immutable-fact preflight unavailable")
    # U08D's full downstream preflight remains authoritative for the complete
    # legacy fact inventory. The external-order permission boundary is one
    # revision above the ledger cutover, though: targeting u8a2 removes u8a3's
    # durable permission ownership while preserving ledger tables. Recheck the
    # exact U08A fact classifier at the current head so u8a3 cannot become the
    # first (partially committed) blocker in a deep downgrade.
    if _destination_is_below("u8a3f4a5b6c7") and bind.execute(
        sa.text(parent_revision.module._EXTERNAL_ORDER_BLOCKING_FACTS)
    ).scalar_one():
        raise RuntimeError("u9a3 downgrade blocked: immutable external order ledger facts exist")
    parent_revision.module._downstream_fact_preflight()


def _create_guard() -> None:
    op.execute(
        r"""CREATE FUNCTION public.guard_pilot_fact_authority_u09() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$
        DECLARE owner_name name;
        BEGIN
          IF TG_TABLE_NAME IN ('pilot_milestones','pilot_milestone_corrections','pilot_authority_receipts') THEN
            RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='pilot fact is immutable';
          END IF;
          SELECT role.rolname INTO owner_name FROM pg_class class
            JOIN pg_roles role ON role.oid=class.relowner WHERE class.oid=TG_RELID;
          IF current_user<>owner_name THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='retrospective mutation requires authority';
          END IF;
          RETURN COALESCE(NEW,OLD);
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_pilot_fact_authority_u09() FROM PUBLIC")
    for table in ("pilot_milestones", "pilot_milestone_corrections", "pilot_authority_receipts"):
        op.execute(
            f"CREATE TRIGGER trg_guard_{table}_u09 BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_pilot_fact_authority_u09()"
        )
    op.execute(
        "CREATE TRIGGER trg_guard_retrospectives_u09 BEFORE INSERT OR UPDATE OR DELETE ON public.retrospectives "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_pilot_fact_authority_u09()"
    )


def _create_retrospective_functions() -> None:
    op.execute(
        r"""CREATE FUNCTION public.update_pending_retrospective_authority(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_retrospective_id uuid,
          requested_request_id uuid,requested_expected_version bigint,requested_goal text,requested_issues text,
          requested_actions jsonb,requested_next_review_date date,requested_idempotency_key text,
          requested_payload_digest text)
        RETURNS TABLE(retrospective_id uuid,version bigint,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE actor record;retro public.retrospectives%ROWTYPE;prior public.pilot_authority_receipts%ROWTYPE;
        BEGIN
          SELECT * INTO actor FROM public.assert_pilot_tenant_actor(requested_tenant_id,
            requested_auth_session_id,'campaign:manage','campaigns',requested_retrospective_id);
          IF requested_request_id IS NULL OR (get_byte(uuid_send(requested_request_id),6)>>4)<>7
             OR requested_expected_version<1 OR btrim(COALESCE(requested_idempotency_key,''))=''
             OR length(requested_idempotency_key)>128 OR requested_payload_digest !~ '^[0-9a-f]{64}$'
             OR (requested_actions IS NOT NULL AND jsonb_typeof(requested_actions)<>'array') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid retrospective update'; END IF;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(COALESCE(requested_actions,'[]'::jsonb)) action
            LEFT JOIN public.accounts owner ON owner.id=CASE
              WHEN action->>'owner_id'~*'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              THEN (action->>'owner_id')::uuid ELSE NULL END
            WHERE action->>'owner_id' IS NOT NULL AND (
              owner.id IS NULL OR NOT owner.is_active OR
              (actor.agency_authorization_id IS NULL AND owner.tenant_id<>requested_tenant_id) OR
              (actor.agency_authorization_id IS NOT NULL AND
                (owner.tenant_id<>actor.actor_tenant_id OR owner.id<>actor.actor_account_id)))
          ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='retrospective action owner denied'; END IF;
          SELECT * INTO prior FROM public.pilot_authority_receipts receipt
          WHERE receipt.tenant_id=requested_tenant_id AND receipt.operation='update_retrospective'
            AND receipt.idempotency_key=btrim(requested_idempotency_key) FOR UPDATE;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='retrospective update idempotency conflict'; END IF;
            RETURN QUERY SELECT prior.resource_id,prior.resource_version,true;RETURN;
          END IF;
          SELECT * INTO retro FROM public.retrospectives value
          WHERE value.tenant_id=requested_tenant_id AND value.id=requested_retrospective_id FOR UPDATE;
          IF retro.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='retrospective unavailable'; END IF;
          IF retro.status::text<>'pending' OR retro.version<>requested_expected_version THEN
            RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='retrospective version conflict'; END IF;
          UPDATE public.retrospectives AS target SET goal=COALESCE(requested_goal,target.goal),
            issues=COALESCE(requested_issues,target.issues),actions=COALESCE(requested_actions::json,target.actions),
            next_review_date=COALESCE(requested_next_review_date,target.next_review_date),
            version=target.version+1,updated_at=statement_timestamp()
          WHERE target.tenant_id=requested_tenant_id AND target.id=requested_retrospective_id RETURNING target.* INTO retro;
          INSERT INTO public.pilot_authority_receipts(id,tenant_id,operation,idempotency_key,payload_digest,
            resource_id,resource_version,actor_type,actor_tenant_id,actor_account_id,auth_session_id,
            agency_authorization_id,result)
          VALUES(requested_request_id,requested_tenant_id,'update_retrospective',
            btrim(requested_idempotency_key),requested_payload_digest,retro.id,retro.version,
            CASE WHEN actor.agency_authorization_id IS NULL THEN 'brand' ELSE 'agency' END,
            actor.actor_tenant_id,actor.actor_account_id,requested_auth_session_id,
            actor.agency_authorization_id,json_build_object('retrospective_id',retro.id,'version',retro.version));
          RETURN QUERY SELECT retro.id,retro.version,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_UPDATE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_UPDATE} TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.complete_retrospective_authority(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_retrospective_id uuid,
          requested_request_id uuid,requested_expected_version bigint,requested_goal text,requested_issues text,
          requested_actions jsonb,requested_next_review_date date,requested_supplementary_note text,
          requested_idempotency_key text,requested_payload_digest text)
        RETURNS TABLE(retrospective_id uuid,version bigint,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE actor record;retro public.retrospectives%ROWTYPE;prior public.pilot_authority_receipts%ROWTYPE;
          task_before text;task_rows integer:=0;
        BEGIN
          SELECT * INTO actor FROM public.assert_pilot_tenant_actor(requested_tenant_id,
            requested_auth_session_id,'campaign:manage','campaigns',requested_retrospective_id);
          IF requested_request_id IS NULL OR (get_byte(uuid_send(requested_request_id),6)>>4)<>7
             OR requested_expected_version<1 OR btrim(COALESCE(requested_idempotency_key,''))=''
             OR length(requested_idempotency_key)>128 OR requested_payload_digest !~ '^[0-9a-f]{64}$'
             OR (requested_actions IS NOT NULL AND jsonb_typeof(requested_actions)<>'array') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid retrospective completion'; END IF;
          SELECT * INTO prior FROM public.pilot_authority_receipts receipt
          WHERE receipt.tenant_id=requested_tenant_id AND receipt.operation='complete_retrospective'
            AND receipt.idempotency_key=btrim(requested_idempotency_key) FOR UPDATE;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='retrospective completion idempotency conflict'; END IF;
            RETURN QUERY SELECT prior.resource_id,prior.resource_version,true;RETURN;
          END IF;
          SELECT * INTO retro FROM public.retrospectives value
          WHERE value.tenant_id=requested_tenant_id AND value.id=requested_retrospective_id FOR UPDATE;
          IF retro.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='retrospective unavailable'; END IF;
          IF retro.status::text<>'pending' OR retro.version<>requested_expected_version THEN
            RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='retrospective completion conflict'; END IF;
          SELECT task.status::text INTO task_before FROM public.ops_tasks task
          WHERE task.tenant_id=requested_tenant_id AND task.id=retro.ops_task_id FOR UPDATE;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(COALESCE(requested_actions,retro.actions::jsonb,'[]'::jsonb)) action
            LEFT JOIN public.accounts owner ON owner.id=CASE
              WHEN action->>'owner_id'~*'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              THEN (action->>'owner_id')::uuid ELSE NULL END
            WHERE action->>'owner_id' IS NOT NULL AND (
              owner.id IS NULL OR NOT owner.is_active OR
              (actor.agency_authorization_id IS NULL AND owner.tenant_id<>requested_tenant_id) OR
              (actor.agency_authorization_id IS NOT NULL AND
                (owner.tenant_id<>actor.actor_tenant_id OR owner.id<>actor.actor_account_id)))
          ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='retrospective action owner denied'; END IF;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(COALESCE(requested_actions,retro.actions::jsonb,'[]'::jsonb)) action
            WHERE COALESCE((action->>'carryover')::boolean,false)
              AND COALESCE(action->>'carryover_disposition','') NOT IN ('continue','adjust','abandon')
          ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='carryover action disposition required'; END IF;
          UPDATE public.retrospectives AS target SET goal=COALESCE(requested_goal,target.goal),
            issues=COALESCE(requested_issues,target.issues),actions=COALESCE(requested_actions::json,target.actions),
            next_review_date=COALESCE(requested_next_review_date,target.next_review_date),status='completed',
            completed_at=statement_timestamp(),completed_by=actor.actor_account_id,
            completed_actor_tenant_id=actor.actor_tenant_id,
            completed_auth_session_id=requested_auth_session_id,
            completed_agency_authorization_id=actor.agency_authorization_id,
            completion_request_id=requested_request_id,
            supplementary_notes=COALESCE(requested_supplementary_note,target.supplementary_notes),
            version=target.version+1,authority_version=1,updated_at=statement_timestamp()
          WHERE target.tenant_id=requested_tenant_id AND target.id=requested_retrospective_id RETURNING target.* INTO retro;
          UPDATE public.ops_tasks SET status='completed',updated_at=statement_timestamp()
          WHERE tenant_id=requested_tenant_id AND id=retro.ops_task_id
            AND status::text NOT IN ('completed','cancelled');
          GET DIAGNOSTICS task_rows=ROW_COUNT;
          INSERT INTO public.pilot_authority_receipts(id,tenant_id,operation,idempotency_key,payload_digest,
            resource_id,resource_version,actor_type,actor_tenant_id,actor_account_id,auth_session_id,
            agency_authorization_id,result)
          VALUES(requested_request_id,requested_tenant_id,'complete_retrospective',
            btrim(requested_idempotency_key),requested_payload_digest,retro.id,retro.version,
            CASE WHEN actor.agency_authorization_id IS NULL THEN 'brand' ELSE 'agency' END,
            actor.actor_tenant_id,actor.actor_account_id,requested_auth_session_id,
            actor.agency_authorization_id,json_build_object('retrospective_id',retro.id,'version',retro.version,
              'ops_task_id',retro.ops_task_id,'ops_task_before_status',task_before,
              'ops_task_after_status',CASE WHEN task_rows=1 THEN 'completed' ELSE task_before END,
              'ops_task_changed',task_rows=1));
          RETURN QUERY SELECT retro.id,retro.version,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_COMPLETE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_COMPLETE} TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.append_retrospective_note_authority(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_retrospective_id uuid,
          requested_request_id uuid,requested_expected_version bigint,requested_note text,
          requested_idempotency_key text,requested_payload_digest text)
        RETURNS TABLE(retrospective_id uuid,version bigint,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE actor record;retro public.retrospectives%ROWTYPE;prior public.pilot_authority_receipts%ROWTYPE;
        BEGIN
          SELECT * INTO actor FROM public.assert_pilot_tenant_actor(requested_tenant_id,
            requested_auth_session_id,'campaign:manage','campaigns',requested_retrospective_id);
          IF requested_request_id IS NULL OR (get_byte(uuid_send(requested_request_id),6)>>4)<>7
             OR requested_expected_version<1 OR btrim(COALESCE(requested_note,''))=''
             OR length(requested_note)>5000 OR btrim(COALESCE(requested_idempotency_key,''))=''
             OR length(requested_idempotency_key)>128 OR requested_payload_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid retrospective note'; END IF;
          SELECT * INTO prior FROM public.pilot_authority_receipts receipt
          WHERE receipt.tenant_id=requested_tenant_id AND receipt.operation='append_retrospective_note'
            AND receipt.idempotency_key=btrim(requested_idempotency_key) FOR UPDATE;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='retrospective note idempotency conflict'; END IF;
            RETURN QUERY SELECT prior.resource_id,prior.resource_version,true;RETURN;
          END IF;
          SELECT * INTO retro FROM public.retrospectives value
          WHERE value.tenant_id=requested_tenant_id AND value.id=requested_retrospective_id FOR UPDATE;
          IF retro.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='retrospective unavailable'; END IF;
          IF retro.status::text<>'completed' OR retro.version<>requested_expected_version THEN
            RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='retrospective note conflict'; END IF;
          UPDATE public.retrospectives AS target SET supplementary_notes=CASE
              WHEN NULLIF(btrim(target.supplementary_notes),'') IS NULL THEN btrim(requested_note)
              ELSE target.supplementary_notes||E'\n'||btrim(requested_note) END,
            version=target.version+1,updated_at=statement_timestamp()
          WHERE target.tenant_id=requested_tenant_id AND target.id=requested_retrospective_id RETURNING target.* INTO retro;
          INSERT INTO public.pilot_authority_receipts(id,tenant_id,operation,idempotency_key,payload_digest,
            resource_id,resource_version,actor_type,actor_tenant_id,actor_account_id,auth_session_id,
            agency_authorization_id,result)
          VALUES(requested_request_id,requested_tenant_id,'append_retrospective_note',
            btrim(requested_idempotency_key),requested_payload_digest,retro.id,retro.version,
            CASE WHEN actor.agency_authorization_id IS NULL THEN 'brand' ELSE 'agency' END,
            actor.actor_tenant_id,actor.actor_account_id,requested_auth_session_id,
            actor.agency_authorization_id,json_build_object('retrospective_id',retro.id,'version',retro.version,
              'note_digest',encode(digest(convert_to(btrim(requested_note),'UTF8'),'sha256'),'hex')));
          RETURN QUERY SELECT retro.id,retro.version,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_NOTE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_NOTE} TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.materialize_due_retrospective(
          requested_tenant_id uuid,requested_retrospective_id uuid,requested_ops_task_id uuid,
          requested_period_day integer,requested_window_start timestamptz,requested_window_end timestamptz,
          requested_next_review_date date,requested_scorecard jsonb,requested_snapshot_digest text,
          requested_assigned_to uuid)
        RETURNS TABLE(retrospective_id uuid,created boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE first_launch timestamptz;expected_end timestamptz;existing_id uuid;existing_digest text;
          resolved_owner uuid;carried_actions jsonb:='[]'::jsonb;
        BEGIN
          IF session_user='yimatong_app' OR NOT has_parameter_privilege(session_user,'app.bypass_rls','SET')
             OR public.current_tenant_id() IS NOT NULL
             OR current_setting('app.bypass_rls',true)<>'true' THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='retrospective generation denied'; END IF;
          IF requested_period_day NOT IN (7,14,30) OR requested_retrospective_id IS NULL
             OR (get_byte(uuid_send(requested_retrospective_id),6)>>4)<>7
             OR requested_ops_task_id IS NULL OR (get_byte(uuid_send(requested_ops_task_id),6)>>4)<>7
             OR requested_scorecard IS NULL OR requested_snapshot_digest !~ '^[0-9a-f]{64}$'
             OR requested_snapshot_digest<>encode(digest(convert_to(requested_scorecard::text,'UTF8'),'sha256'),'hex') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid retrospective generation'; END IF;
          SELECT min(launched_at) INTO first_launch FROM public.launch_releases
          WHERE tenant_id=requested_tenant_id AND launched_at IS NOT NULL;
          expected_end:=first_launch+make_interval(days=>requested_period_day);
          IF first_launch IS NULL OR requested_window_start<>first_launch OR requested_window_end<>expected_end
             OR requested_next_review_date<>(CASE requested_period_day
                  WHEN 7 THEN (first_launch+interval '14 days')::date
                  WHEN 14 THEN (first_launch+interval '30 days')::date
                  ELSE (expected_end+interval '30 days')::date END)
             OR statement_timestamp()<expected_end THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='retrospective is not due'; END IF;
          IF NOT pg_try_advisory_xact_lock(hashtextextended(
            'pilot-retrospective:'||requested_tenant_id::text||':'||requested_period_day::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='retrospective generation busy'; END IF;
          SELECT id,snapshot_digest INTO existing_id,existing_digest FROM public.retrospectives
          WHERE tenant_id=requested_tenant_id AND period_day=requested_period_day;
          IF existing_id IS NOT NULL THEN
            IF existing_digest<>requested_snapshot_digest THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='retrospective generation payload conflict'; END IF;
            RETURN QUERY SELECT existing_id,false;RETURN;
          END IF;
          SELECT COALESCE(jsonb_agg(jsonb_build_object(
              'content',action->>'content','owner_id',action->'owner_id','due_date',action->'due_date',
              'status','pending','carryover',true,'carryover_disposition',NULL) ORDER BY ordinal),'[]'::jsonb)
          INTO carried_actions
          FROM public.retrospectives previous
          CROSS JOIN LATERAL jsonb_array_elements(previous.actions::jsonb) WITH ORDINALITY AS valueset(action,ordinal)
          WHERE previous.tenant_id=requested_tenant_id AND previous.period_day=(
              SELECT max(candidate.period_day) FROM public.retrospectives candidate
              WHERE candidate.tenant_id=requested_tenant_id AND candidate.period_day<requested_period_day)
            AND COALESCE(action->>'status','')<>'completed';
          IF requested_assigned_to IS NOT NULL THEN
            SELECT account.id INTO resolved_owner FROM public.accounts account
            JOIN public.account_roles ar ON ar.tenant_id=account.tenant_id AND ar.account_id=account.id
            JOIN public.roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN public.role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id
            JOIN public.permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            WHERE account.id=requested_assigned_to AND account.is_active
              AND role.name IN('admin','operator') AND permission.code='campaign:manage'
              AND EXISTS(SELECT 1 FROM public.agency_authorizations authz
                WHERE authz.agency_tenant_id=account.tenant_id AND authz.client_tenant_id=requested_tenant_id
                  AND authz.status::text='active' AND authz.revoked_at IS NULL
                  AND (authz.expires_at IS NULL OR authz.expires_at>statement_timestamp())
                  AND authz.scope::jsonb ? 'campaigns') LIMIT 1;
            IF resolved_owner IS NULL THEN
              RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='retrospective owner denied'; END IF;
          ELSE
            SELECT account.id INTO resolved_owner FROM public.accounts account
            JOIN public.account_roles ar ON ar.tenant_id=account.tenant_id AND ar.account_id=account.id
            JOIN public.roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN public.role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id
            JOIN public.permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            JOIN public.agency_authorizations authz ON authz.agency_tenant_id=account.tenant_id
            WHERE authz.client_tenant_id=requested_tenant_id AND authz.status::text='active'
              AND authz.revoked_at IS NULL AND (authz.expires_at IS NULL OR authz.expires_at>statement_timestamp())
              AND authz.scope::jsonb ? 'campaigns' AND account.is_active
              AND role.name IN('admin','operator') AND permission.code='campaign:manage'
            ORDER BY account.last_login_at DESC NULLS LAST,account.created_at DESC,account.id LIMIT 1;
          END IF;
          INSERT INTO public.ops_tasks(id,tenant_id,assigned_to,title,description,status,priority,due_date,
            created_at,updated_at) VALUES(requested_ops_task_id,requested_tenant_id,resolved_owner,
            '试点复盘第'||requested_period_day||'天待填写',
            '请完成上线后第 '||requested_period_day||' 天的试点复盘（里程碑/漏斗数据已预填）。',
            'pending','medium',requested_next_review_date::timestamptz,statement_timestamp(),statement_timestamp());
          INSERT INTO public.retrospectives(id,tenant_id,period_day,window_start,window_end,next_review_date,
            status,scorecard_snapshot,actions,ops_task_id,created_at,updated_at,version,snapshot_digest,
            authority_version)
          VALUES(requested_retrospective_id,requested_tenant_id,requested_period_day,requested_window_start,
            requested_window_end,requested_next_review_date,'pending',requested_scorecard::json,carried_actions::json,
            requested_ops_task_id,statement_timestamp(),statement_timestamp(),1,requested_snapshot_digest,1);
          RETURN QUERY SELECT requested_retrospective_id,true;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_GENERATE} FROM PUBLIC")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    _create_guard()
    _create_retrospective_functions()
    op.execute(
        "ALTER TABLE public.pilot_milestones ADD CONSTRAINT ck_pilot_milestones_authority_u09 "
        "CHECK(authority_version IN(0,1) AND fact_digest~'^[0-9a-f]{64}$') NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.pilot_milestone_corrections ADD CONSTRAINT ck_pilot_corrections_authority_u09 "
        "CHECK(authority_version IN(0,1) AND request_id=id AND actor_type IN('legacy','platform') "
        "AND payload_digest~'^[0-9a-f]{64}$' AND btrim(idempotency_key)<>'') NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.retrospectives ADD CONSTRAINT ck_retrospectives_authority_u09 "
        "CHECK(authority_version IN(0,1) AND version>=1 AND snapshot_digest~'^[0-9a-f]{64}$' "
        "AND period_day IN(7,14,30) AND window_end>window_start "
        "AND (authority_version=0 OR ((status='pending' AND completed_at IS NULL AND completed_by IS NULL "
        "AND completion_request_id IS NULL) OR (status='completed' AND completed_at IS NOT NULL "
        "AND completed_by IS NOT NULL AND completed_actor_tenant_id IS NOT NULL "
        "AND completed_auth_session_id IS NOT NULL AND completion_request_id IS NOT NULL)))) NOT VALID"
    )
    for table, constraint in (
        ("pilot_milestones", "ck_pilot_milestones_authority_u09"),
        ("pilot_milestone_corrections", "ck_pilot_corrections_authority_u09"),
        ("retrospectives", "ck_retrospectives_authority_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_actor_account_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_auth_session_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_platform_session_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_agency_auth_u09"),
        ("pilot_milestone_corrections", "fk_pilot_correction_platform_session_u09"),
        ("retrospectives", "fk_retrospective_completed_account_u09"),
        ("retrospectives", "fk_retrospective_completed_session_u09"),
        ("retrospectives", "fk_retrospective_completed_agency_u09"),
        ("retrospectives", "fk_retrospective_completion_receipt_u09"),
    ):
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {constraint}")
    for table, columns in (
        ("pilot_milestones", ("fact_digest", "authority_version")),
        (
            "pilot_milestone_corrections",
            ("request_id", "actor_type", "actor_principal", "idempotency_key", "payload_digest", "authority_version"),
        ),
        ("retrospectives", ("version", "snapshot_digest", "authority_version")),
    ):
        for column in columns:
            op.alter_column(table, column, nullable=False)
    for table in ("pilot_milestones", "pilot_milestone_corrections", "retrospectives"):
        op.execute(f"REVOKE ALL PRIVILEGES ON public.{table} FROM yimatong_app")
        op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")
    op.execute("REVOKE ALL PRIVILEGES ON public.pilot_authority_receipts FROM yimatong_app")


def downgrade() -> None:
    _downstream_fact_preflight()
    op.execute("SET LOCAL lock_timeout='5s'")
    for signature in (_GENERATE, _NOTE, _COMPLETE, _UPDATE):
        op.execute(f"DROP FUNCTION public.{signature}")
    for table in ("retrospectives", "pilot_authority_receipts", "pilot_milestone_corrections", "pilot_milestones"):
        op.execute(f"DROP TRIGGER trg_guard_{table}_u09 ON public.{table}")
    op.execute("DROP FUNCTION public.guard_pilot_fact_authority_u09()")
    for table, columns in (
        ("pilot_milestones", ("fact_digest", "authority_version")),
        (
            "pilot_milestone_corrections",
            ("request_id", "actor_type", "actor_principal", "idempotency_key", "payload_digest", "authority_version"),
        ),
        ("retrospectives", ("version", "snapshot_digest", "authority_version")),
    ):
        for column in columns:
            op.alter_column(table, column, nullable=True)
    op.drop_constraint("ck_retrospectives_authority_u09", "retrospectives", type_="check")
    op.drop_constraint("ck_pilot_corrections_authority_u09", "pilot_milestone_corrections", type_="check")
    op.drop_constraint("ck_pilot_milestones_authority_u09", "pilot_milestones", type_="check")
    for table in ("pilot_milestones", "pilot_milestone_corrections", "retrospectives"):
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON public.{table} TO yimatong_app")
