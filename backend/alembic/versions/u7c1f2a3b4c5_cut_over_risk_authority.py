"""cut over risk decisions to durable database authority

Revision ID: u7c1f2a3b4c5
Revises: u7c0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u7c1f2a3b4c5"
down_revision: str | Sequence[str] | None = "u7c0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "yimatong_app"
_PUBLIC = (
    "mutate_risk_rule(uuid,uuid,uuid,text,uuid,bigint,text,text,text,text,jsonb,boolean)",
    "set_campaign_risk_rule(uuid,uuid,uuid,uuid,uuid,boolean,text)",
    "resume_risk_campaign_pause(uuid,uuid,uuid,uuid,bigint,text,text)",
    "evaluate_execute_scan_risk(uuid,uuid,uuid,uuid,text,jsonb)",
)
_RESTRICTED = (
    "risk_rules", "campaign_risk_rules", "interception_records", "risk_alerts", "risk_notifications",
    "risk_action_receipts", "risk_campaign_pauses", "risk_action_outbox",
)


def _role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": _ROLE}).scalar())


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("risk authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    """Fail at the current head before a deep downgrade can commit partial steps."""

    bind = op.get_bind()
    risk_facts = bind.execute(sa.text("SELECT count(*) FROM risk_action_receipts")).scalar_one()
    if risk_facts and _destination_is_below("u7c0e1f2a3b4"):
        raise RuntimeError("u7c1 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4"):
        diversion_facts = bind.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM diversion_observations)+"
                "(SELECT count(*) FROM diversion_action_receipts)+"
                "(SELECT count(*) FROM diversion_evidence)+"
                "(SELECT count(*) FROM diversion_investigation_history)"
            )
        ).scalar_one()
        if diversion_facts:
            raise RuntimeError("u7c1 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4"):
        channel_facts = bind.execute(sa.text("SELECT count(*) FROM channel_action_receipts")).scalar_one()
        if channel_facts:
            raise RuntimeError("u7c1 downgrade blocked: channel action receipts are immutable facts")


def _permissions() -> None:
    op.create_table(
        "risk_permission_backfill",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.Column("created_permission", sa.Boolean(), nullable=False),
        sa.Column("created_grant", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "role_id", "permission_code", name="pk_risk_permission_backfill"),
    )
    op.execute("REVOKE ALL ON public.risk_permission_backfill FROM PUBLIC")
    op.execute(r"""
      WITH targets AS MATERIALIZED (
        SELECT DISTINCT role.tenant_id,wanted.code
        FROM roles role CROSS JOIN LATERAL (VALUES ('risk:read'),('risk:manage'),('risk:evaluate')) wanted(code)
        WHERE role.name IN ('admin','operator')
      ), permission_targets AS MATERIALIZED (
        SELECT target.tenant_id,target.code,COALESCE(permission.id,gen_random_uuid()) permission_id,
          permission.id IS NULL created_permission
        FROM targets target LEFT JOIN permissions permission
          ON permission.tenant_id=target.tenant_id AND permission.code=target.code
      ), desired AS (
        SELECT role.tenant_id,role.id role_id,target.code,target.permission_id,target.created_permission,
          rp.role_id IS NULL created_grant
        FROM roles role JOIN permission_targets target ON target.tenant_id=role.tenant_id
        LEFT JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id
          AND rp.permission_id=target.permission_id
        WHERE role.name IN ('admin','operator')
      )
      INSERT INTO risk_permission_backfill
        (tenant_id,role_id,permission_id,permission_code,created_permission,created_grant)
      SELECT tenant_id,role_id,permission_id,code,created_permission,created_grant FROM desired
      ON CONFLICT DO NOTHING
    """)
    op.execute(r"""
      INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at)
      SELECT DISTINCT ON (tenant_id,permission_code)
        permission_id,tenant_id,permission_code,'风控权威权限',now(),now()
      FROM risk_permission_backfill WHERE created_permission
      ORDER BY tenant_id,permission_code,permission_id ON CONFLICT DO NOTHING
    """)
    op.execute(r"""
      INSERT INTO role_permissions(tenant_id,role_id,permission_id)
      SELECT tenant_id,role_id,permission_id FROM risk_permission_backfill WHERE created_grant
      ON CONFLICT DO NOTHING
    """)


def _execute_function_batch(sql: str) -> None:
    """asyncpg prepares one DDL command at a time; keep function bodies intact."""
    chunks = sql.strip().split("\n\n    CREATE FUNCTION")
    for index, chunk in enumerate(chunks):
        op.execute(chunk if index == 0 else "CREATE FUNCTION" + chunk)


def _functions() -> None:
    sql = r"""
    CREATE FUNCTION public.authorize_risk_actor(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_permission text
    ) RETURNS TABLE(actor_id uuid,principal_tenant_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
    BEGIN
      IF requested_permission NOT IN ('risk:read','risk:manage','risk:evaluate') THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk permission is invalid'; END IF;
      IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='risk tenant context mismatch'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_auth_session_id::text,0));
      SELECT account.id,session.tenant_id INTO actor_id,principal_tenant_id
      FROM auth_sessions session JOIN accounts account
        ON account.tenant_id=session.tenant_id AND account.id=session.account_id
      JOIN tenants tenant ON tenant.id=session.tenant_id
      WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
        AND session.revoked_at IS NULL AND session.expires_at>now_at
        AND session.auth_version=account.auth_version AND account.is_active AND tenant.status='active';
      IF actor_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='risk auth session is not live'; END IF;
      IF NOT EXISTS(SELECT 1 FROM account_roles ar JOIN role_permissions rp
          ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
        JOIN permissions p ON p.tenant_id=rp.tenant_id AND p.id=rp.permission_id
        WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=actor_id AND p.code=requested_permission)
      THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='risk permission denied'; END IF;
      RETURN NEXT;
    END $fn$;

    CREATE FUNCTION public.mutate_risk_rule(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_action text,
      requested_rule_id uuid,requested_expected_version bigint,requested_idempotency_key text,
      requested_name text,requested_rule_type text,requested_rule_action text,requested_config jsonb,
      requested_enabled boolean
    ) RETURNS TABLE(rule_id uuid,version bigint,enabled boolean,replayed boolean,actor_id uuid,recorded_at timestamptz)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;DECLARE rule risk_rules%ROWTYPE;
    DECLARE digest_value text;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE result_value jsonb;
    DECLARE resolved_receipt_id uuid:=gen_random_uuid();
    BEGIN
      SELECT a.actor_id INTO resolved_actor FROM authorize_risk_actor(requested_tenant_id,requested_auth_session_id,'risk:manage') a;
      IF requested_audit_id IS NULL OR requested_rule_id IS NULL OR requested_action NOT IN ('create','update','disable')
        OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128
        OR NULLIF(trim(requested_name),'') IS NULL OR length(trim(requested_name))>200
        OR NULLIF(trim(requested_rule_type),'') IS NULL OR length(trim(requested_rule_type))>50
        OR requested_rule_action NOT IN ('warn','block') OR requested_config IS NULL
      THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk rule mutation input is invalid'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('risk-rule-idem:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
      digest_value:=encode(digest(convert_to(jsonb_build_array(requested_action,requested_rule_id,
        requested_expected_version,trim(requested_name),trim(requested_rule_type),requested_rule_action,
        requested_config,requested_enabled)::text,'UTF8'),'sha256'),'hex');
      SELECT * INTO existing FROM risk_action_receipts r WHERE r.tenant_id=requested_tenant_id
        AND r.action='rule:'||requested_action AND r.idempotency_key=requested_idempotency_key;
      IF FOUND THEN
        IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='risk rule idempotency conflict'; END IF;
        rule_id:=(existing.result->>'rule_id')::uuid;version:=(existing.result->>'version')::bigint;
        enabled:=(existing.result->>'enabled')::boolean;replayed:=true;actor_id:=existing.actor_id;
        recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
      END IF;
      IF requested_action='create' THEN
        IF requested_expected_version IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='new risk rule cannot have expected version'; END IF;
        INSERT INTO risk_rules(id,tenant_id,name,rule_type,action,config,enabled,version,created_at,updated_at)
        VALUES(requested_rule_id,requested_tenant_id,trim(requested_name),trim(requested_rule_type),requested_rule_action,
          requested_config,requested_enabled,1,now_at,now_at) RETURNING * INTO rule;
      ELSE
        SELECT * INTO rule FROM risk_rules r WHERE r.tenant_id=requested_tenant_id AND r.id=requested_rule_id FOR UPDATE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk rule is unavailable'; END IF;
        IF requested_expected_version IS DISTINCT FROM rule.version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='risk rule version conflict'; END IF;
        UPDATE risk_rules r SET name=trim(requested_name),rule_type=trim(requested_rule_type),
          action=requested_rule_action,config=requested_config,
          enabled=CASE WHEN requested_action='disable' THEN false ELSE requested_enabled END,
          version=r.version+1,updated_at=now_at WHERE r.id=rule.id RETURNING * INTO rule;
      END IF;
      result_value:=jsonb_build_object('rule_id',rule.id,'version',rule.version,'enabled',rule.enabled);
      INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,risk_rule_id,actor_id,result,recorded_at)
      VALUES(resolved_receipt_id,requested_tenant_id,'rule:'||requested_action,requested_idempotency_key,digest_value,rule.id,resolved_actor,result_value,now_at);
      INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
      VALUES(gen_random_uuid(),requested_tenant_id,resolved_receipt_id,'risk.rule_mutated',
        jsonb_build_object('tenant_id',requested_tenant_id,'action','rule:'||requested_action,
          'resource','risk_rule:'||rule.id::text,'result_version',1,'actor_id',resolved_actor,
          'receipt_id',resolved_receipt_id,'result',result_value),0,now_at);
      INSERT INTO platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
      VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'risk_rule_'||requested_action,
        'risk_rule:'||rule.id::text,result_value,now_at,now_at,now_at);
      rule_id:=rule.id;version:=rule.version;enabled:=rule.enabled;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
    EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk rule is concurrently changing';
    END $fn$;

    CREATE FUNCTION public.set_campaign_risk_rule(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
      requested_rule_id uuid,requested_campaign_id uuid,requested_attach boolean,requested_idempotency_key text
    ) RETURNS TABLE(link_id uuid,attached boolean,replayed boolean,actor_id uuid,recorded_at timestamptz)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;DECLARE resolved_link uuid;
    DECLARE digest_value text;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE result_value jsonb;
    DECLARE resolved_receipt_id uuid:=gen_random_uuid();
    BEGIN
      SELECT a.actor_id INTO resolved_actor FROM authorize_risk_actor(requested_tenant_id,requested_auth_session_id,'risk:manage') a;
      IF requested_audit_id IS NULL OR requested_rule_id IS NULL OR requested_campaign_id IS NULL
        OR requested_attach IS NULL OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128
      THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign risk rule input is invalid'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('risk-link-idem:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
      digest_value:=encode(digest(convert_to(jsonb_build_array(requested_rule_id,requested_campaign_id,requested_attach)::text,'UTF8'),'sha256'),'hex');
      SELECT * INTO existing FROM risk_action_receipts r WHERE r.tenant_id=requested_tenant_id
        AND r.action='campaign_rule' AND r.idempotency_key=requested_idempotency_key;
      IF FOUND THEN
        IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='campaign risk rule idempotency conflict'; END IF;
        link_id:=(existing.result->>'link_id')::uuid;attached:=(existing.result->>'attached')::boolean;
        replayed:=true;actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
      END IF;
      PERFORM id FROM risk_rules WHERE tenant_id=requested_tenant_id AND id=requested_rule_id FOR SHARE NOWAIT;
      IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk rule is unavailable'; END IF;
      PERFORM id FROM campaigns WHERE tenant_id=requested_tenant_id AND id=requested_campaign_id FOR SHARE NOWAIT;
      IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign is unavailable'; END IF;
      SELECT id INTO resolved_link FROM campaign_risk_rules WHERE tenant_id=requested_tenant_id
        AND campaign_id=requested_campaign_id AND risk_rule_id=requested_rule_id FOR UPDATE;
      IF requested_attach AND resolved_link IS NULL THEN
        resolved_link:=gen_random_uuid();INSERT INTO campaign_risk_rules(id,tenant_id,campaign_id,risk_rule_id,created_at,updated_at)
        VALUES(resolved_link,requested_tenant_id,requested_campaign_id,requested_rule_id,now_at,now_at);
      ELSIF NOT requested_attach AND resolved_link IS NOT NULL THEN
        DELETE FROM campaign_risk_rules WHERE id=resolved_link;
      END IF;
      result_value:=jsonb_build_object('link_id',resolved_link,'attached',requested_attach);
      INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,risk_rule_id,actor_id,result,recorded_at)
      VALUES(resolved_receipt_id,requested_tenant_id,'campaign_rule',requested_idempotency_key,digest_value,requested_rule_id,resolved_actor,result_value,now_at);
      INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
      VALUES(gen_random_uuid(),requested_tenant_id,resolved_receipt_id,'risk.campaign_rule_set',
        jsonb_build_object('tenant_id',requested_tenant_id,
          'action',CASE WHEN requested_attach THEN 'campaign_rule:attach' ELSE 'campaign_rule:detach' END,
          'resource','campaign:'||requested_campaign_id::text,'result_version',1,'actor_id',resolved_actor,
          'receipt_id',resolved_receipt_id,'result',result_value),0,now_at);
      INSERT INTO platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
      VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,
        CASE WHEN requested_attach THEN 'risk_rule_attached' ELSE 'risk_rule_detached' END,
        'campaign:'||requested_campaign_id::text,result_value,now_at,now_at,now_at);
      link_id:=resolved_link;attached:=requested_attach;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
    EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk link authority is concurrently changing';
    END $fn$;
    """
    _execute_function_batch(sql)
    _install_evaluate_and_resume()


def _install_evaluate_and_resume() -> None:
    sql = r"""
    CREATE FUNCTION public.evaluate_execute_scan_risk(
      requested_tenant_id uuid,requested_scan_event_id uuid,requested_rule_id uuid,requested_receipt_id uuid,
      requested_idempotency_key text,requested_context jsonb
    ) RETURNS TABLE(receipt_id uuid,interception_id uuid,triggered boolean,action text,alert_id uuid,
      paused_campaign_ids uuid[],replayed boolean,recorded_at timestamptz)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE existing risk_action_receipts%ROWTYPE;DECLARE scan scan_events%ROWTYPE;DECLARE rule risk_rules%ROWTYPE;
    DECLARE item record;DECLARE threshold_value bigint;DECLARE observed_count bigint;DECLARE should_trigger boolean:=false;
    DECLARE context_snapshot jsonb;DECLARE digest_value text;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
    DECLARE resolved_interception uuid;DECLARE resolved_alert uuid;DECLARE campaign record;DECLARE pause_ids uuid[]:='{}';
    DECLARE canonical_pause record;
    DECLARE result_value jsonb;DECLARE freeze_audit uuid;DECLARE freeze_result record;
    BEGIN
      IF current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='risk evaluation tenant context mismatch'; END IF;
      IF requested_scan_event_id IS NULL OR requested_rule_id IS NULL OR requested_receipt_id IS NULL
        OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128
        OR requested_context IS NULL OR jsonb_typeof(requested_context)<>'object'
        OR length(requested_context::text)>4096
        OR length(COALESCE(requested_context->>'request_source',''))>50
      THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk evaluation input is invalid'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('risk-eval:'||requested_tenant_id::text||':'||requested_scan_event_id::text||':'||requested_rule_id::text,0));
      digest_value:=encode(digest(convert_to(jsonb_build_array(requested_scan_event_id,requested_rule_id,requested_context)::text,'UTF8'),'sha256'),'hex');
      SELECT * INTO existing FROM risk_action_receipts r WHERE r.tenant_id=requested_tenant_id
        AND ((r.action='evaluate' AND r.idempotency_key=requested_idempotency_key)
          OR (r.scan_event_id=requested_scan_event_id AND r.risk_rule_id=requested_rule_id));
      IF FOUND THEN
        IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='risk evaluation idempotency conflict'; END IF;
        receipt_id:=existing.id;interception_id:=(existing.result->>'interception_id')::uuid;
        triggered:=(existing.result->>'triggered')::boolean;action:=existing.result->>'action';
        alert_id:=(existing.result->>'alert_id')::uuid;
        SELECT COALESCE(array_agg(value::uuid),'{}'::uuid[]) INTO paused_campaign_ids FROM json_array_elements_text(existing.result->'paused_campaign_ids');
        replayed:=true;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
      END IF;
      SELECT * INTO scan FROM scan_events s WHERE s.tenant_id=requested_tenant_id AND s.id=requested_scan_event_id FOR SHARE;
      IF scan.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk scan event is unavailable'; END IF;
      SELECT ci.id,ci.public_id,cb.product_id INTO item FROM code_items ci JOIN code_batches cb
        ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id
        WHERE ci.tenant_id=requested_tenant_id AND ci.public_id=scan.public_id;
      IF item.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk scan subject is unavailable'; END IF;
      SELECT * INTO rule FROM risk_rules r WHERE r.tenant_id=requested_tenant_id AND r.id=requested_rule_id AND r.enabled FOR SHARE NOWAIT;
      IF rule.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk rule is unavailable'; END IF;
      threshold_value:=GREATEST(COALESCE((rule.config->>'threshold')::bigint,(rule.config->>'max_requests')::bigint,1),1);
      SELECT count(*) INTO observed_count FROM scan_events s WHERE s.tenant_id=requested_tenant_id
        AND s.public_id=scan.public_id AND s.scan_time BETWEEN scan.scan_time-interval '24 hours' AND scan.scan_time;
      should_trigger:=CASE
        WHEN rule.rule_type IN ('ip_frequency','phone_frequency','device_frequency','scan_frequency') THEN observed_count>=threshold_value
        WHEN rule.rule_type='cross_region' THEN EXISTS(SELECT 1 FROM diversion_observations o WHERE o.tenant_id=requested_tenant_id AND o.scan_event_id=scan.id)
        ELSE COALESCE((rule.config->>'always_trigger')::boolean,false) END;
      context_snapshot:=jsonb_build_object('scan_event_id',scan.id,'scan_time',scan.scan_time,'public_id',scan.public_id,
        'ip_hash',scan.ip_hash,'rule_id',rule.id,'rule_version',rule.version,
        'config_digest',encode(digest(convert_to(rule.config::text,'UTF8'),'sha256'),'hex'),
        'observed_count_24h',observed_count,'threshold',threshold_value,
        'request_source',COALESCE(requested_context->>'request_source','worker'));
      INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,scan_event_id,risk_rule_id,result,recorded_at)
      VALUES(requested_receipt_id,requested_tenant_id,'evaluate',requested_idempotency_key,digest_value,scan.id,rule.id,'{}',now_at);
      IF should_trigger THEN
        resolved_interception:=gen_random_uuid();
        INSERT INTO interception_records(id,tenant_id,risk_rule_id,code_item_id,action,context,auto_triggered,created_at,updated_at)
        VALUES(resolved_interception,requested_tenant_id,rule.id,item.id,rule.action,context_snapshot,true,now_at,now_at);
        resolved_alert:=gen_random_uuid();
        IF rule.action='block' THEN
          freeze_audit:=gen_random_uuid();
          SELECT * INTO freeze_result FROM freeze_code_item_for_risk(
            requested_tenant_id,resolved_interception,item.id,resolved_alert,freeze_audit
          );
          UPDATE risk_alerts SET risk_rule_id=rule.id,risk_action_receipt_id=requested_receipt_id,
            source='worker_evaluation',reason_snapshot=left('risk auto block rule='||rule.id::text,200),
            prior_code_status=freeze_result.prior_status,current_code_status=freeze_result.current_status
          WHERE tenant_id=requested_tenant_id AND id=resolved_alert;
          FOR campaign IN SELECT c.id,c.status FROM campaign_risk_rules link JOIN campaigns c
              ON c.tenant_id=link.tenant_id AND c.id=link.campaign_id
            WHERE link.tenant_id=requested_tenant_id AND link.risk_rule_id=rule.id
              AND c.product_id=item.product_id AND (c.status='active' OR EXISTS(
                SELECT 1 FROM risk_campaign_pauses p WHERE p.tenant_id=c.tenant_id AND p.campaign_id=c.id AND p.status='active'))
            ORDER BY c.id::text FOR UPDATE OF c NOWAIT
          LOOP
            SELECT prior_status,authority_updated_at INTO canonical_pause FROM risk_campaign_pauses p
            WHERE p.tenant_id=requested_tenant_id AND p.campaign_id=campaign.id AND p.status='active'
            ORDER BY p.paused_at,p.id::text LIMIT 1;
            IF canonical_pause.prior_status IS NULL THEN
              canonical_pause.prior_status:=campaign.status;
              UPDATE campaigns SET status='paused',updated_at=now_at WHERE id=campaign.id
              RETURNING * INTO campaign;
              canonical_pause.authority_updated_at:=campaign.updated_at;
            END IF;
            INSERT INTO risk_campaign_pauses(id,tenant_id,receipt_id,campaign_id,risk_rule_id,prior_status,
              authority_updated_at,status,version,paused_at)
            VALUES(gen_random_uuid(),requested_tenant_id,requested_receipt_id,campaign.id,rule.id,
              canonical_pause.prior_status,canonical_pause.authority_updated_at,'active',1,now_at);
            pause_ids:=array_append(pause_ids,campaign.id);
          END LOOP;
        ELSE
          INSERT INTO risk_alerts(id,tenant_id,alert_type,public_id,code_item_id,risk_rule_id,
            risk_action_receipt_id,source,reason_snapshot,prior_code_status,current_code_status,
            detail,ip_hash,resolved,risk_level,rule_version,evidence_quality,rule_name,created_at,updated_at)
          VALUES(resolved_alert,requested_tenant_id,'suspected_copy',item.public_id,item.id,rule.id,
            requested_receipt_id,'worker_evaluation','risk rule warning',NULL,NULL,
            '风控规则自动触发：仅预警',scan.ip_hash,false,'medium',rule.version::text,'medium',left(rule.name,100),now_at,now_at);
          UPDATE interception_records SET action_taken='warn',updated_at=now_at WHERE id=resolved_interception;
        END IF;
      END IF;
      result_value:=jsonb_build_object('interception_id',resolved_interception,'triggered',should_trigger,
        'action',CASE WHEN should_trigger THEN rule.action ELSE 'none' END,'alert_id',resolved_alert,
        'paused_campaign_ids',to_jsonb(pause_ids),'context_snapshot',context_snapshot);
      UPDATE risk_action_receipts SET result=result_value WHERE id=requested_receipt_id;
      INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
      VALUES(gen_random_uuid(),requested_tenant_id,requested_receipt_id,'risk.action_evaluated',result_value,0,now_at);
      receipt_id:=requested_receipt_id;interception_id:=resolved_interception;triggered:=should_trigger;
      action:=CASE WHEN should_trigger THEN rule.action ELSE 'none' END;alert_id:=resolved_alert;
      paused_campaign_ids:=pause_ids;replayed:=false;recorded_at:=now_at;RETURN NEXT;
    EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk decision authority is concurrently changing';
    END $fn$;

    CREATE FUNCTION public.resume_risk_campaign_pause(
      requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_pause_id uuid,
      requested_expected_version bigint,requested_idempotency_key text,requested_reason text
    ) RETURNS TABLE(pause_id uuid,campaign_id uuid,version bigint,status text,campaign_status text,
      replayed boolean,actor_id uuid,recorded_at timestamptz)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
    DECLARE resolved_actor uuid;DECLARE existing risk_action_receipts%ROWTYPE;DECLARE pause risk_campaign_pauses%ROWTYPE;
    DECLARE campaign campaigns%ROWTYPE;DECLARE digest_value text;DECLARE result_value jsonb;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
    DECLARE resolved_receipt_id uuid:=gen_random_uuid();
    BEGIN
      SELECT a.actor_id INTO resolved_actor FROM authorize_risk_actor(requested_tenant_id,requested_auth_session_id,'risk:manage') a;
      IF requested_audit_id IS NULL OR requested_pause_id IS NULL OR requested_expected_version IS NULL
        OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128
        OR NULLIF(trim(requested_reason),'') IS NULL OR length(trim(requested_reason))>2000
      THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='risk pause resume input is invalid'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('risk-resume-idem:'||requested_tenant_id::text||':'||requested_idempotency_key,0));
      digest_value:=encode(digest(convert_to(jsonb_build_array(requested_pause_id,requested_expected_version,trim(requested_reason))::text,'UTF8'),'sha256'),'hex');
      SELECT * INTO existing FROM risk_action_receipts r WHERE r.tenant_id=requested_tenant_id AND r.action='resume_pause' AND r.idempotency_key=requested_idempotency_key;
      IF FOUND THEN
        IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='risk resume idempotency conflict'; END IF;
        pause_id:=(existing.result->>'pause_id')::uuid;campaign_id:=(existing.result->>'campaign_id')::uuid;
        version:=(existing.result->>'version')::bigint;status:=existing.result->>'status';campaign_status:=existing.result->>'campaign_status';
        replayed:=true;actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
      END IF;
      SELECT * INTO pause FROM risk_campaign_pauses p WHERE p.tenant_id=requested_tenant_id AND p.id=requested_pause_id FOR UPDATE NOWAIT;
      IF pause.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk campaign pause is unavailable'; END IF;
      IF pause.status<>'active' OR pause.version<>requested_expected_version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='risk campaign pause version conflict'; END IF;
      SELECT * INTO campaign FROM campaigns c WHERE c.tenant_id=requested_tenant_id AND c.id=pause.campaign_id FOR UPDATE NOWAIT;
      IF campaign.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='risk campaign is unavailable'; END IF;
      UPDATE risk_campaign_pauses p SET status='resumed',version=p.version+1,resumed_at=now_at,
        resumed_by_account_id=resolved_actor,resume_reason=trim(requested_reason) WHERE p.id=pause.id RETURNING * INTO pause;
      IF NOT EXISTS(SELECT 1 FROM risk_campaign_pauses p WHERE p.tenant_id=requested_tenant_id
        AND p.campaign_id=campaign.id AND p.status='active') THEN
        IF campaign.status='paused' AND campaign.updated_at=pause.authority_updated_at THEN
          UPDATE campaigns SET status=pause.prior_status,updated_at=now_at
          WHERE id=campaign.id RETURNING * INTO campaign;
        END IF;
      END IF;
      result_value:=jsonb_build_object('pause_id',pause.id,'campaign_id',campaign.id,'version',pause.version,
        'status',pause.status,'campaign_status',campaign.status);
      INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,risk_rule_id,actor_id,result,recorded_at)
      VALUES(resolved_receipt_id,requested_tenant_id,'resume_pause',requested_idempotency_key,digest_value,pause.risk_rule_id,resolved_actor,result_value,now_at);
      INSERT INTO risk_action_outbox(id,tenant_id,receipt_id,topic,payload,attempts,created_at)
      VALUES(gen_random_uuid(),requested_tenant_id,resolved_receipt_id,'risk.campaign_resumed',
        jsonb_build_object('tenant_id',requested_tenant_id,'action','resume_pause',
          'resource','campaign:'||campaign.id::text,'result_version',1,'actor_id',resolved_actor,
          'receipt_id',resolved_receipt_id,'result',result_value),0,now_at);
      INSERT INTO platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
      VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'risk_campaign_resumed',
        'campaign:'||campaign.id::text,result_value||jsonb_build_object('reason',trim(requested_reason)),now_at,now_at,now_at);
      pause_id:=pause.id;campaign_id:=campaign.id;version:=pause.version;status:=pause.status;campaign_status:=campaign.status;
      replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;RETURN NEXT;
    EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='risk pause authority is concurrently changing';
    END $fn$;
    """
    _execute_function_batch(sql)


def upgrade() -> None:
    _permissions()
    _functions()
    op.alter_column("risk_rules", "version", server_default=None)
    op.alter_column("risk_campaign_pauses", "status", server_default=None)
    op.alter_column("risk_campaign_pauses", "version", server_default=None)
    op.alter_column("risk_action_outbox", "attempts", server_default=None)
    op.execute("REVOKE ALL ON FUNCTION public.authorize_risk_actor(uuid,uuid,text) FROM PUBLIC")
    if _role_exists():
        op.execute(f"REVOKE ALL ON FUNCTION public.authorize_risk_actor(uuid,uuid,text) FROM {_ROLE}")
    for signature in _PUBLIC:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_ROLE}")
    if _role_exists():
        for table in _RESTRICTED:
            op.execute(f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON public.{table} FROM {_ROLE}")
            op.execute(f"GRANT SELECT ON public.{table} TO {_ROLE}")


def downgrade() -> None:
    _downgrade_preflight()
    op.alter_column("risk_action_outbox", "attempts", server_default="0")
    op.alter_column("risk_campaign_pauses", "version", server_default="1")
    op.alter_column("risk_campaign_pauses", "status", server_default="active")
    op.alter_column("risk_rules", "version", server_default="1")
    for signature in reversed(_PUBLIC):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("DROP FUNCTION IF EXISTS public.authorize_risk_actor(uuid,uuid,text)")
    op.execute("DELETE FROM role_permissions rp USING risk_permission_backfill marker WHERE marker.created_grant AND rp.tenant_id=marker.tenant_id AND rp.role_id=marker.role_id AND rp.permission_id=marker.permission_id")
    op.execute("DELETE FROM permissions p USING risk_permission_backfill marker WHERE marker.created_permission AND p.tenant_id=marker.tenant_id AND p.id=marker.permission_id")
    op.drop_table("risk_permission_backfill")
