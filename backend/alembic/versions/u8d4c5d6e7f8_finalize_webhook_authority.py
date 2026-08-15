"""finalize webhook authority

Revision ID: u8d4c5d6e7f8
Revises: u8d3b4c5d6e7
"""

from collections.abc import Sequence
from datetime import UTC

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u8d4c5d6e7f8"
down_revision: str | Sequence[str] | None = "u8d3b4c5d6e7"
branch_labels = None
depends_on = None

_MARKER = "webhook_permission_backfill"
_CAMPAIGN_SIGNATURE = "public.transition_campaign(uuid,uuid,uuid,uuid,text)"
_CAMPAIGN_HANDOFF = r"""WITH event_envelope AS (
                SELECT '{"data":{"campaign_id":"'||requested_campaign_id::text||'","status":"'||
                    requested_status||'"},"id":"'||requested_audit_id::text||'","tenant_id":"'||
                    requested_tenant_id::text||'","timestamp":"'||
                    to_char(now_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||
                    '+00:00","type":"'||event_name||'"}' AS canonical_payload
            )
            INSERT INTO public.webhook_domain_events(
                id,tenant_id,event_type,payload,payload_digest,occurred_at
            ) SELECT requested_audit_id,requested_tenant_id,event_name,canonical_payload::jsonb,
                encode(digest(convert_to(canonical_payload,'UTF8'),'sha256'),'hex'),now_at
            FROM event_envelope;"""
_LEGACY_CAMPAIGN_INSERT = """INSERT INTO public.webhook_deliveries(
                id,tenant_id,endpoint_id,event_id,event_type,payload,status,retry_count,
                next_retry_at,created_at,updated_at
            ) SELECT gen_random_uuid(),requested_tenant_id,endpoint.id,requested_audit_id::text,event_name,
                jsonb_build_object('campaign_id',requested_campaign_id,'status',requested_status,
                    'published_at',row_value.published_at),
                'pending',0,now_at,now_at,now_at
            FROM public.webhook_endpoints AS endpoint
            WHERE endpoint.tenant_id=requested_tenant_id AND endpoint.enabled
              AND endpoint.events::jsonb ? event_name;"""

_EXTERNAL_ORDER_BLOCKING_FACTS = """
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


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("webhook authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downstream_fact_preflight() -> None:
    """Block a deep downgrade at head before any later revision can commit."""

    bind = op.get_bind()
    if _destination_is_below("u8d0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM public.webhook_domain_events")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: durable webhook event facts exist")
    if _destination_is_below("u8c4d5e6f7a8") and bind.execute(
        sa.text("SELECT count(*) FROM public.export_logs WHERE authority_version IN (1,2)")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable prepared export facts exist")
    if _destination_is_below("u8b2e3f4a5b6") and bind.execute(
        sa.text("SELECT count(*) FROM public.gmv_attribution_confirmations")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable confirmed attribution evidence exists")
    if _destination_is_below("u8a2e3f4a5b6") and bind.execute(
        sa.text(_EXTERNAL_ORDER_BLOCKING_FACTS)
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable external order ledger facts exist")
    if _destination_is_below("u7c0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM public.risk_action_receipts")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM public.diversion_observations)+"
            "(SELECT count(*) FROM public.diversion_action_receipts)+"
            "(SELECT count(*) FROM public.diversion_evidence)+"
            "(SELECT count(*) FROM public.diversion_investigation_history)"
        )
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4") and bind.execute(
        sa.text("SELECT count(*) FROM public.channel_action_receipts")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: channel action receipts are immutable facts")
    if _destination_is_below("u6l4a5b6c7d8") and bind.execute(
        sa.text("SELECT count(*) FROM public.wecom_callback_receipts")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable verified WeCom callback receipts exist")
    if _destination_is_below("u6k1d2e3f4a5") and bind.execute(
        sa.text("SELECT count(*) FROM public.campaign_delivery_callback_attempts")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: campaign delivery callback attempts are immutable facts")
    if _destination_is_below("u6f0a1b2c3d4") and bind.execute(
        sa.text("SELECT count(*) FROM public.consumer_consent_actions WHERE action='wechat_bind'")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: immutable consumer OAuth binding audits exist")
    if _destination_is_below("u6e0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM public.consumer_consent_actions)+"
            "(SELECT count(*) FROM public.consent_records WHERE authority_version=1)+"
            "(SELECT count(*) FROM public.consumer_profiles WHERE lead_contact_suppressed IS TRUE "
            "OR lead_consent_id IS NOT NULL OR lead_scan_event_id IS NOT NULL "
            "OR lead_scan_event_time IS NOT NULL OR lead_captured_at IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: authoritative consumer consent facts exist")
    if _destination_is_below("u6b5c6d7e8f9") and bind.execute(
        sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: bound benefit claims are immutable facts")
    if _destination_is_below("u6a3d4e5f6a7") and bind.execute(
        sa.text("SELECT count(*) FROM public.campaign_claim_outbox")
    ).scalar_one():
        raise RuntimeError("u8d4 downgrade blocked: campaign claim delivery facts are immutable")


def _replace_campaign_delivery(old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"), {"signature": _CAMPAIGN_SIGNATURE}
    ).scalar_one()
    if definition.count(old) != 1:
        raise RuntimeError("u8d4 campaign webhook handoff source drifted")
    bind.execute(sa.text(definition.replace(old, new)))


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _snapshot_legacy_deliveries() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT delivery.id,delivery.tenant_id,delivery.event_id,delivery.event_type,delivery.payload,"
            "delivery.created_at,endpoint.url,endpoint.secret_ciphertext,endpoint.secret_nonce,"
            "endpoint.secret_key_id,endpoint.config_version FROM webhook_deliveries delivery "
            "JOIN webhook_endpoints endpoint ON endpoint.tenant_id=delivery.tenant_id "
            "AND endpoint.id=delivery.endpoint_id WHERE delivery.domain_event_id IS NULL "
            "ORDER BY delivery.tenant_id,delivery.id"
        )
    ).mappings()
    for row in rows:
        try:
            event_id = str(uuid.UUID(row["event_id"]))
        except (TypeError, ValueError):
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"yimatong:webhook-delivery:{row['id']}"))
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        is_envelope = (
            payload.get("id") == event_id
            and payload.get("type") == row["event_type"]
            and payload.get("tenant_id") == str(row["tenant_id"])
            and isinstance(payload.get("timestamp"), str)
            and "data" in payload
        )
        envelope = payload if is_envelope else {
            "id": event_id,
            "type": row["event_type"],
            "timestamp": row["created_at"].astimezone(UTC).isoformat(),
            "tenant_id": str(row["tenant_id"]),
            "data": payload,
        }
        bind.execute(
            sa.text(
                "UPDATE webhook_deliveries SET event_id=:event_id,payload=CAST(:payload AS json),"
                "payload_digest=:payload_digest,endpoint_url=:endpoint_url,"
                "endpoint_secret_ciphertext=:ciphertext,endpoint_secret_nonce=:nonce,"
                "endpoint_secret_key_id=:key_id,endpoint_config_version=:config_version,"
                "legacy_payload_wrapped=:wrapped WHERE id=:id AND tenant_id=:tenant_id"
            ),
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "event_id": event_id,
                "payload": _canonical(envelope).decode(),
                "payload_digest": hashlib.sha256(_canonical(envelope)).hexdigest(),
                "endpoint_url": row["url"],
                "ciphertext": row["secret_ciphertext"],
                "nonce": row["secret_nonce"],
                "key_id": row["secret_key_id"],
                "config_version": row["config_version"],
                "wrapped": not is_envelope,
            },
        )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    # Sole cutover boundary: close inserts/endpoint rotation between the
    # legacy snapshot pass and the trigger that rejects future legacy rows.
    op.execute("LOCK TABLE webhook_endpoints,webhook_deliveries IN SHARE ROW EXCLUSIVE MODE")
    _replace_campaign_delivery(_LEGACY_CAMPAIGN_INSERT, _CAMPAIGN_HANDOFF)
    op.execute("ALTER TABLE webhook_deliveries DISABLE TRIGGER trg_guard_webhook_delivery_snapshot")
    try:
        _snapshot_legacy_deliveries()
    finally:
        op.execute("ALTER TABLE webhook_deliveries ENABLE TRIGGER trg_guard_webhook_delivery_snapshot")
    op.create_table(
        _MARKER,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.Column("created_permission", sa.Boolean(), nullable=False),
        sa.Column("created_grant", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "role_id", "permission_code", name="pk_webhook_permission_backfill"),
    )
    op.execute(f"REVOKE ALL ON public.{_MARKER} FROM PUBLIC")
    op.execute(r"""WITH desired_codes AS (
      SELECT role.tenant_id,role.id role_id,wanted.code
      FROM roles role CROSS JOIN LATERAL (VALUES ('webhook:read'),('webhook:manage')) wanted(code)
      WHERE role.name='admin'
    ), resolved AS (
      SELECT desired.*,COALESCE(permission.id,gen_random_uuid()) permission_id,
        permission.id IS NULL created_permission
      FROM desired_codes desired LEFT JOIN permissions permission
        ON permission.tenant_id=desired.tenant_id AND permission.code=desired.code
    )
    INSERT INTO webhook_permission_backfill(tenant_id,role_id,permission_id,permission_code,created_permission,created_grant)
    SELECT resolved.tenant_id,resolved.role_id,resolved.permission_id,resolved.code,resolved.created_permission,
      rp.role_id IS NULL FROM resolved LEFT JOIN role_permissions rp
      ON rp.tenant_id=resolved.tenant_id AND rp.role_id=resolved.role_id AND rp.permission_id=resolved.permission_id""")
    op.execute("INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) SELECT DISTINCT permission_id,tenant_id,permission_code,'Webhook endpoint authority',statement_timestamp(),statement_timestamp() FROM webhook_permission_backfill WHERE created_permission ON CONFLICT DO NOTHING")
    op.execute("INSERT INTO role_permissions(tenant_id,role_id,permission_id) SELECT tenant_id,role_id,permission_id FROM webhook_permission_backfill WHERE created_grant ON CONFLICT DO NOTHING")
    # Existing legacy rows remain retryable, but no new delivery may bypass a
    # tenant-bound immutable domain event after the final cutover.
    op.execute(r"""CREATE FUNCTION public.reject_legacy_webhook_delivery_insert_u8d() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
      RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='legacy webhook delivery insert is disabled'; END $fn$""")
    op.execute("REVOKE ALL ON FUNCTION public.reject_legacy_webhook_delivery_insert_u8d() FROM PUBLIC")
    op.execute("CREATE TRIGGER trg_00_reject_legacy_webhook_delivery_insert_u8d BEFORE INSERT ON webhook_deliveries FOR EACH ROW WHEN (NEW.domain_event_id IS NULL) EXECUTE FUNCTION reject_legacy_webhook_delivery_insert_u8d()")
    op.drop_column("webhook_endpoints", "secret")


def downgrade() -> None:
    from app.utils.crypto import EnvKeyProvider, decrypt_bytes, init_crypto

    _downstream_fact_preflight()
    op.execute("SET LOCAL lock_timeout='5s'")
    init_crypto(EnvKeyProvider())
    op.execute("DROP TRIGGER trg_00_reject_legacy_webhook_delivery_insert_u8d ON webhook_deliveries")
    op.execute("DROP FUNCTION reject_legacy_webhook_delivery_insert_u8d()")
    op.execute("ALTER TABLE webhook_deliveries DISABLE TRIGGER trg_guard_webhook_delivery_snapshot")
    op.execute(
        "UPDATE webhook_deliveries SET payload=CASE WHEN legacy_payload_wrapped THEN payload->'data' ELSE payload END,"
        "payload_digest=NULL,endpoint_url=NULL,endpoint_secret_ciphertext=NULL,endpoint_secret_nonce=NULL,"
        "endpoint_secret_key_id=NULL,endpoint_config_version=NULL,legacy_payload_wrapped=NULL "
        "WHERE domain_event_id IS NULL"
    )
    op.execute("ALTER TABLE webhook_deliveries ENABLE TRIGGER trg_guard_webhook_delivery_snapshot")
    _replace_campaign_delivery(_CAMPAIGN_HANDOFF, _LEGACY_CAMPAIGN_INSERT)
    op.add_column("webhook_endpoints", sa.Column("secret", sa.String(100), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id,tenant_id,secret_ciphertext,secret_nonce,secret_key_id FROM webhook_endpoints ORDER BY tenant_id,id")).mappings()
    for row in rows:
        aad = f"webhook-endpoint:{row['tenant_id']}:{row['id']}".encode()
        secret = decrypt_bytes(bytes(row["secret_ciphertext"]), nonce=bytes(row["secret_nonce"]), key_id=row["secret_key_id"], aad=aad).decode()
        bind.execute(sa.text("UPDATE webhook_endpoints SET secret=:secret WHERE tenant_id=:tenant_id AND id=:id"), {**row, "secret": secret})
    op.alter_column("webhook_endpoints", "secret", nullable=False)
    op.execute("DELETE FROM role_permissions rp USING webhook_permission_backfill marker WHERE marker.created_grant AND rp.tenant_id=marker.tenant_id AND rp.role_id=marker.role_id AND rp.permission_id=marker.permission_id")
    op.execute("DELETE FROM permissions permission USING webhook_permission_backfill marker WHERE marker.created_permission AND permission.tenant_id=marker.tenant_id AND permission.id=marker.permission_id AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.tenant_id=permission.tenant_id AND rp.permission_id=permission.id)")
    op.drop_table(_MARKER)
