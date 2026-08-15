"""bind manual risk freeze to durable alert authority

Revision ID: u7c2a3b4c5d6
Revises: u7c1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u7c2a3b4c5d6"
down_revision: str | Sequence[str] | None = "u7c1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "yimatong_app"
_SIGNATURE = "freeze_code_item_with_risk_alert(uuid,uuid,uuid,uuid,uuid,uuid,text,text)"
_NOTIFICATION_SIGNATURES = (
    "mark_risk_notification_read(uuid,uuid,uuid,uuid,text)",
    "mark_all_risk_notifications_read(uuid,uuid,uuid,text)",
)


def _role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": _ROLE}).scalar())


def _execute_function_batch(sql: str) -> None:
    """asyncpg prepares one DDL command at a time; keep function bodies intact."""
    chunks = sql.strip().split("\n\n      CREATE FUNCTION")
    for index, chunk in enumerate(chunks):
        op.execute(chunk if index == 0 else "CREATE FUNCTION" + chunk)


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("manual risk freeze downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def upgrade() -> None:
    _execute_function_batch(r"""
      CREATE FUNCTION public.guard_risk_alert_snapshot()
      RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      BEGIN
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.alert_type IS DISTINCT FROM OLD.alert_type
          OR NEW.public_id IS DISTINCT FROM OLD.public_id OR NEW.code_item_id IS DISTINCT FROM OLD.code_item_id
          OR NEW.detail IS DISTINCT FROM OLD.detail OR NEW.ip_hash IS DISTINCT FROM OLD.ip_hash
          OR NEW.risk_level IS DISTINCT FROM OLD.risk_level OR NEW.rule_version IS DISTINCT FROM OLD.rule_version
          OR NEW.evidence_quality IS DISTINCT FROM OLD.evidence_quality OR NEW.rule_name IS DISTINCT FROM OLD.rule_name THEN
          RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='risk alert snapshot is immutable';
        END IF;
        IF NEW.risk_rule_id IS DISTINCT FROM OLD.risk_rule_id OR NEW.risk_action_receipt_id IS DISTINCT FROM OLD.risk_action_receipt_id
          OR NEW.actor_id IS DISTINCT FROM OLD.actor_id OR NEW.source IS DISTINCT FROM OLD.source
          OR NEW.reason_snapshot IS DISTINCT FROM OLD.reason_snapshot OR NEW.prior_code_status IS DISTINCT FROM OLD.prior_code_status
          OR NEW.current_code_status IS DISTINCT FROM OLD.current_code_status THEN
          IF OLD.risk_rule_id IS NOT NULL OR OLD.risk_action_receipt_id IS NOT NULL OR OLD.actor_id IS NOT NULL
            OR OLD.source IS NOT NULL OR OLD.reason_snapshot IS NOT NULL OR OLD.prior_code_status IS NOT NULL
            OR OLD.current_code_status IS NOT NULL OR NEW.source IS DISTINCT FROM 'worker_evaluation' THEN
            RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='risk alert authority binding is immutable';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.guard_risk_alert_snapshot() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_risk_alert_snapshot BEFORE UPDATE ON public.risk_alerts "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_risk_alert_snapshot()"
    )
    op.execute(r"""
      CREATE FUNCTION public.freeze_code_item_with_risk_alert(
        requested_tenant_id uuid,requested_auth_session_id uuid,requested_receipt_id uuid,
        requested_audit_id uuid,requested_alert_id uuid,requested_code_item_id uuid,
        requested_idempotency_key text,requested_reason text
      ) RETURNS TABLE(code_item_id uuid,prior_status text,current_status text,risk_alert_id uuid,
        receipt_id uuid,replayed boolean,actor_id uuid,recorded_at timestamptz)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;
      DECLARE transition_result record;DECLARE item record;DECLARE digest_value text;
      DECLARE result_value jsonb;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
      BEGIN
        SELECT authority.actor_id INTO resolved_actor FROM authorize_risk_actor(
          requested_tenant_id,requested_auth_session_id,'risk:manage') authority;
        IF requested_receipt_id IS NULL OR requested_audit_id IS NULL OR requested_alert_id IS NULL
          OR requested_code_item_id IS NULL OR NULLIF(trim(requested_idempotency_key),'') IS NULL
          OR length(requested_idempotency_key)>128 OR NULLIF(trim(requested_reason),'') IS NULL
          OR length(trim(requested_reason))>200 THEN
          RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='manual risk freeze input is invalid';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'risk-manual-freeze:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
        digest_value:=encode(digest(convert_to(
          jsonb_build_array(requested_code_item_id,trim(requested_reason))::text,'UTF8'),'sha256'),'hex');
        SELECT * INTO existing FROM risk_action_receipts receipt
        WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='manual_freeze'
          AND receipt.idempotency_key=requested_idempotency_key;
        IF FOUND THEN
          IF existing.payload_digest<>digest_value THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='manual risk freeze idempotency conflict'; END IF;
          code_item_id:=(existing.result->>'code_item_id')::uuid;
          prior_status:=existing.result->>'prior_status';current_status:=existing.result->>'current_status';
          risk_alert_id:=(existing.result->>'risk_alert_id')::uuid;receipt_id:=existing.id;
          replayed:=true;actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
        END IF;
        SELECT * INTO transition_result FROM transition_code_item_lifecycle(
          requested_tenant_id,requested_auth_session_id,requested_audit_id,
          requested_code_item_id,'freeze',trim(requested_reason));
        SELECT ci.public_id INTO item FROM code_items ci
        WHERE ci.tenant_id=requested_tenant_id AND ci.id=requested_code_item_id;
        IF item.public_id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk code item is unavailable'; END IF;
        result_value:=jsonb_build_object('code_item_id',requested_code_item_id,
          'prior_status',transition_result.prior_status,'current_status',transition_result.current_status,
          'risk_alert_id',requested_alert_id,'audit_id',requested_audit_id);
        INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,actor_id,result,recorded_at)
        VALUES(requested_receipt_id,requested_tenant_id,'manual_freeze',requested_idempotency_key,
          digest_value,resolved_actor,result_value,now_at);
        INSERT INTO risk_alerts(id,tenant_id,alert_type,public_id,code_item_id,risk_action_receipt_id,
          actor_id,source,reason_snapshot,prior_code_status,current_code_status,detail,resolved,created_at,updated_at)
        VALUES(requested_alert_id,requested_tenant_id,'risk_frozen',item.public_id,requested_code_item_id,
          requested_receipt_id,resolved_actor,'manual_freeze',trim(requested_reason),transition_result.prior_status,
          transition_result.current_status,'码已被风险冻结',false,now_at,now_at);
        INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
        VALUES(gen_random_uuid(),requested_tenant_id,requested_receipt_id,'risk.code_item_frozen',result_value,0,now_at);
        code_item_id:=requested_code_item_id;prior_status:=transition_result.prior_status;
        current_status:=transition_result.current_status;risk_alert_id:=requested_alert_id;
        receipt_id:=requested_receipt_id;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
      EXCEPTION WHEN lock_not_available THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='manual risk freeze is concurrently changing';
      END $fn$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_SIGNATURE} FROM PUBLIC")
    _execute_function_batch(r"""
      CREATE FUNCTION public.mark_risk_notification_read(
        requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
        requested_notification_id uuid,requested_idempotency_key text
      ) RETURNS TABLE(notification_id uuid,read boolean,changed boolean,receipt_id uuid,
        replayed boolean,actor_id uuid,recorded_at timestamptz)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;DECLARE notification record;
      DECLARE digest_value text;DECLARE result_value jsonb;DECLARE resolved_receipt_id uuid:=gen_random_uuid();
      DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
      BEGIN
        SELECT authority.actor_id INTO resolved_actor FROM authorize_risk_actor(
          requested_tenant_id,requested_auth_session_id,'risk:manage') authority;
        IF requested_audit_id IS NULL OR requested_notification_id IS NULL
          OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128 THEN
          RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk notification read input is invalid'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'risk-notification-read:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
        digest_value:=encode(digest(convert_to(jsonb_build_array(requested_notification_id)::text,'UTF8'),'sha256'),'hex');
        SELECT * INTO existing FROM risk_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id
          AND receipt.action='notification:read' AND receipt.idempotency_key=requested_idempotency_key;
        IF FOUND THEN
          IF existing.payload_digest<>digest_value THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='risk notification read idempotency conflict'; END IF;
          notification_id:=(existing.result->>'notification_id')::uuid;read:=(existing.result->>'read')::boolean;
          changed:=(existing.result->>'changed')::boolean;receipt_id:=existing.id;replayed:=true;
          actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
        END IF;
        SELECT id,n.read INTO notification FROM risk_notifications n
        WHERE n.tenant_id=requested_tenant_id AND n.id=requested_notification_id FOR UPDATE NOWAIT;
        IF notification.id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk notification is unavailable'; END IF;
        changed:=NOT notification.read;
        IF changed THEN UPDATE risk_notifications SET read=true,updated_at=now_at WHERE id=notification.id; END IF;
        result_value:=jsonb_build_object('notification_id',notification.id,'read',true,'changed',changed);
        INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,actor_id,result,recorded_at)
        VALUES(resolved_receipt_id,requested_tenant_id,'notification:read',requested_idempotency_key,
          digest_value,resolved_actor,result_value,now_at);
        INSERT INTO platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
        VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'risk_notification_read',
          'risk_notification:'||notification.id::text,result_value,now_at,now_at,now_at);
        INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
        VALUES(gen_random_uuid(),requested_tenant_id,resolved_receipt_id,'risk.notification_read',
          jsonb_build_object('tenant_id',requested_tenant_id,'action','notification:read',
            'resource','risk_notification:'||notification.id::text,'result_version',1,'actor_id',resolved_actor,
            'receipt_id',resolved_receipt_id,'result',result_value),0,now_at);
        notification_id:=notification.id;read:=true;receipt_id:=resolved_receipt_id;replayed:=false;
        actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
      EXCEPTION WHEN lock_not_available THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk notification is concurrently changing';
      END $fn$;

      CREATE FUNCTION public.mark_all_risk_notifications_read(
        requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
        requested_idempotency_key text
      ) RETURNS TABLE(updated_count bigint,receipt_id uuid,replayed boolean,actor_id uuid,recorded_at timestamptz)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;DECLARE notification record;
      DECLARE digest_value text;DECLARE result_value jsonb;DECLARE resolved_receipt_id uuid:=gen_random_uuid();
      DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
      BEGIN
        SELECT authority.actor_id INTO resolved_actor FROM authorize_risk_actor(
          requested_tenant_id,requested_auth_session_id,'risk:manage') authority;
        IF requested_audit_id IS NULL OR NULLIF(trim(requested_idempotency_key),'') IS NULL
          OR length(requested_idempotency_key)>128 THEN
          RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk notifications read-all input is invalid'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'risk-notifications-read-all:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
        digest_value:=encode(digest(convert_to(jsonb_build_array(requested_tenant_id,'all')::text,'UTF8'),'sha256'),'hex');
        SELECT * INTO existing FROM risk_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id
          AND receipt.action='notifications:read_all' AND receipt.idempotency_key=requested_idempotency_key;
        IF FOUND THEN
          IF existing.payload_digest<>digest_value THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='risk notifications read-all idempotency conflict'; END IF;
          updated_count:=(existing.result->>'updated_count')::bigint;receipt_id:=existing.id;replayed:=true;
          actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
        END IF;
        FOR notification IN SELECT id FROM risk_notifications n WHERE n.tenant_id=requested_tenant_id
          AND NOT n.read ORDER BY n.id::text FOR UPDATE NOWAIT LOOP NULL; END LOOP;
        UPDATE risk_notifications SET read=true,updated_at=now_at
        WHERE tenant_id=requested_tenant_id AND NOT read;
        GET DIAGNOSTICS updated_count=ROW_COUNT;
        result_value:=jsonb_build_object('updated_count',updated_count);
        INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,actor_id,result,recorded_at)
        VALUES(resolved_receipt_id,requested_tenant_id,'notifications:read_all',requested_idempotency_key,
          digest_value,resolved_actor,result_value,now_at);
        INSERT INTO platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
        VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'risk_notifications_read_all',
          'risk_notifications:all',result_value,now_at,now_at,now_at);
        INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
        VALUES(gen_random_uuid(),requested_tenant_id,resolved_receipt_id,'risk.notifications_read_all',
          jsonb_build_object('tenant_id',requested_tenant_id,'action','notifications:read_all',
            'resource','risk_notifications:all','result_version',1,'actor_id',resolved_actor,
            'receipt_id',resolved_receipt_id,'result',result_value),0,now_at);
        receipt_id:=resolved_receipt_id;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
      EXCEPTION WHEN lock_not_available THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk notifications are concurrently changing';
      END $fn$;
    """)
    for signature in _NOTIFICATION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_SIGNATURE} TO {_ROLE}")
        for signature in _NOTIFICATION_SIGNATURES:
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_ROLE}")
        op.execute("REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON public.risk_alerts FROM yimatong_app")
        op.execute("GRANT SELECT ON public.risk_alerts TO yimatong_app")


def downgrade() -> None:
    bind = op.get_bind()
    facts = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM risk_action_receipts WHERE action='manual_freeze')+"
            "(SELECT count(*) FROM risk_action_receipts "
            "WHERE action IN ('notification:read','notifications:read_all'))+"
            "(SELECT count(*) FROM risk_alerts WHERE source='manual_freeze')"
        )
    ).scalar_one()
    if facts:
        raise RuntimeError("u7c2 downgrade blocked: immutable facts: risk freeze or notification receipts exist")
    for signature in reversed(_NOTIFICATION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    if _destination_is_below("u7c0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM risk_action_receipts")
    ).scalar_one():
        raise RuntimeError("u7c2 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM diversion_observations)+"
            "(SELECT count(*) FROM diversion_action_receipts)+"
            "(SELECT count(*) FROM diversion_evidence)+"
            "(SELECT count(*) FROM diversion_investigation_history)"
        )
    ).scalar_one():
        raise RuntimeError("u7c2 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4") and bind.execute(
        sa.text("SELECT count(*) FROM channel_action_receipts")
    ).scalar_one():
        raise RuntimeError("u7c2 downgrade blocked: channel action receipts are immutable facts")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SIGNATURE}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_risk_alert_snapshot ON public.risk_alerts")
    op.execute("DROP FUNCTION IF EXISTS public.guard_risk_alert_snapshot()")
