"""separate legacy delivery retries and preserve callback attempt history

Revision ID: u6k1d2e3f4a5
Revises: u6k0c1d2e3f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "u6k1d2e3f4a5"
down_revision: str | Sequence[str] | None = "u6k0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CALLBACK = "settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
_LEGACY_CALLBACK = "settle_campaign_claim_callback_legacy(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
_DELIVERY_RESULT = "record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)"
_LEGACY_DELIVERY_RESULT = (
    "record_campaign_claim_delivery_result_legacy(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)"
)
_DOWNGRADE_INDEX = "uq_benefit_deliveries_tenant_id_id_downgrade"
_DOWNGRADE_INDEX_EXPECTED = (
    "CREATE UNIQUE INDEX uq_benefit_deliveries_tenant_id_id_downgrade "
    "ON public.benefit_deliveries USING btree (tenant_id, id)"
)


def _role_exists(role: str) -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar())


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("delivery retry authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.campaign_delivery_callback_attempts")).scalar_one():
        raise RuntimeError("cannot downgrade while immutable campaign delivery callback attempts exist")
    if _destination_is_below("u6b5c6d7e8f9") and bind.execute(
        sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("u6k1 downgrade blocked: bound benefit claims are immutable facts")
    if _destination_is_below("u7c0e1f2a3b4") and bind.execute(
        sa.text("SELECT count(*) FROM public.risk_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6k1 downgrade blocked: risk action receipts are immutable facts")
    if _destination_is_below("u7b0c1d2e3f4") and bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM public.diversion_observations)+"
            "(SELECT count(*) FROM public.diversion_action_receipts)+"
            "(SELECT count(*) FROM public.diversion_evidence)+"
            "(SELECT count(*) FROM public.diversion_investigation_history)"
        )
    ).scalar_one():
        raise RuntimeError("u6k1 downgrade blocked: immutable diversion investigation facts exist")
    if _destination_is_below("u7a0c1d2e3f4") and bind.execute(
        sa.text("SELECT count(*) FROM public.channel_action_receipts")
    ).scalar_one():
        raise RuntimeError("u6k1 downgrade blocked: channel action receipts are immutable facts")


def _downgrade_index_facts() -> tuple[bool, bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=:name"
        ),
        {"name": _DOWNGRADE_INDEX},
    ).one_or_none()
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _prepare_downgrade_index() -> None:
    valid, unique, definition = _downgrade_index_facts()
    if valid and unique and definition == _DOWNGRADE_INDEX_EXPECTED:
        return
    if definition is not None and (not unique or definition != _DOWNGRADE_INDEX_EXPECTED):
        raise RuntimeError(f"refusing unexpected downgrade index public.{_DOWNGRADE_INDEX}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_DOWNGRADE_INDEX}")
        try:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {_DOWNGRADE_INDEX} "
                "ON public.benefit_deliveries (tenant_id,id)"
            )
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_DOWNGRADE_INDEX}")
            raise
    valid, unique, definition = _downgrade_index_facts()
    if not valid or not unique or definition != _DOWNGRADE_INDEX_EXPECTED:
        raise RuntimeError(f"downgrade index public.{_DOWNGRADE_INDEX} is not exact and valid")


def upgrade() -> None:
    _prepare_downgrade_index()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        "ALTER TABLE public.benefit_deliveries ADD CONSTRAINT uq_benefit_deliveries_tenant_id_id "
        "UNIQUE USING INDEX uq_benefit_deliveries_tenant_id_id"
    )
    op.create_table(
        "campaign_delivery_callback_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("callback_status", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_campaign_callback_attempts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "delivery_id", "callback_status", name="uq_campaign_callback_attempts_delivery_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "delivery_id"],
            ["benefit_deliveries.tenant_id", "benefit_deliveries.id"],
            name="fk_campaign_callback_attempts_tenant_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_campaign_callback_attempts_tenant_claim",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("callback_status IN ('failed','success')", name="ck_campaign_callback_attempts_status"),
        sa.CheckConstraint("length(payload_digest)=64", name="ck_campaign_callback_attempts_digest"),
    )
    op.create_index(
        "ix_campaign_callback_attempts_delivery_time",
        "campaign_delivery_callback_attempts",
        ["tenant_id", "delivery_id", "recorded_at"],
    )
    op.create_index(
        "ix_campaign_delivery_callback_attempts_tenant_id",
        "campaign_delivery_callback_attempts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_campaign_delivery_callback_attempts_delivery_id",
        "campaign_delivery_callback_attempts",
        ["delivery_id"],
    )
    op.execute("ALTER TABLE public.campaign_delivery_callback_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.campaign_delivery_callback_attempts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.campaign_delivery_callback_attempts "
        "USING (tenant_id=public.current_tenant_id()) WITH CHECK (tenant_id=public.current_tenant_id())"
    )
    op.execute(r"""
      CREATE FUNCTION public.guard_campaign_delivery_callback_attempts()
      RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      BEGIN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='campaign delivery callback attempts are immutable'; END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.guard_campaign_delivery_callback_attempts() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_campaign_delivery_callback_attempts BEFORE UPDATE OR DELETE "
        "ON public.campaign_delivery_callback_attempts FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_campaign_delivery_callback_attempts()"
    )
    op.execute(
        "ALTER FUNCTION public.settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb) "
        "RENAME TO settle_campaign_claim_callback_legacy"
    )
    op.execute(
        "ALTER FUNCTION public.record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer) "
        "RENAME TO record_campaign_claim_delivery_result_legacy"
    )
    op.execute(r"""
      CREATE FUNCTION public.record_campaign_claim_delivery_result(
        requested_tenant_id uuid,requested_outbox_id uuid,requested_lease_token uuid,
        requested_delivery_id uuid,requested_connector_id uuid,requested_result_status text,
        requested_external_id text,requested_external_data jsonb,requested_callback_timeout_seconds integer
      ) RETURNS TABLE(outbox_id uuid,delivery_id uuid,current_status text,claim_delivery_status text,
        attempt_count integer,next_attempt_at timestamptz,delivered_at timestamptz)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      BEGIN
        IF requested_external_data IS NULL OR jsonb_typeof(requested_external_data)<>'object' THEN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='delivery result authority invalid';
        END IF;
        RETURN QUERY SELECT * FROM public.record_campaign_claim_delivery_result_legacy(
          requested_tenant_id,requested_outbox_id,requested_lease_token,requested_delivery_id,
          requested_connector_id,requested_result_status,requested_external_id,'{}'::jsonb,
          requested_callback_timeout_seconds);
      END $fn$
    """)
    op.execute(r"""
      CREATE FUNCTION public.settle_campaign_claim_callback(
        requested_tenant_id uuid,requested_audit_id uuid,requested_connector_id uuid,
        requested_delivery_id uuid,requested_claim_id uuid,requested_external_id text,
        requested_callback_status text,requested_external_data jsonb
      ) RETURNS TABLE(delivery_id uuid,claim_id uuid,outbox_id uuid,current_status text,replayed boolean,
        settled_at timestamptz)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE prior record;DECLARE settled record;DECLARE delivery_row record;DECLARE digest_value text;
      BEGIN
        IF session_user<>'yimatong_callback' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
          OR requested_audit_id IS NULL OR requested_delivery_id IS NULL OR requested_claim_id IS NULL
          OR requested_connector_id IS NULL OR requested_callback_status NOT IN ('failed','success')
          OR NULLIF(trim(requested_external_id),'') IS NULL OR requested_external_data IS NULL
          OR jsonb_typeof(requested_external_data)<>'object' THEN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='connector callback authority invalid'; END IF;
        digest_value:=encode(digest(convert_to(jsonb_build_array(1,requested_tenant_id,requested_connector_id,
          requested_delivery_id,requested_claim_id,trim(requested_external_id),requested_callback_status,
          requested_external_data)::text,'UTF8'),'sha256'),'hex');
        SELECT * INTO delivery_row FROM benefit_deliveries d WHERE d.tenant_id=requested_tenant_id
          AND d.id=requested_delivery_id AND d.claim_id=requested_claim_id
          AND d.connector_id=requested_connector_id AND d.external_id=trim(requested_external_id) FOR UPDATE NOWAIT;
        IF NOT FOUND OR delivery_row.campaign_outbox_id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector delivery relationship unavailable'; END IF;
        SELECT * INTO prior FROM campaign_delivery_callback_attempts a WHERE a.tenant_id=requested_tenant_id
          AND a.delivery_id=requested_delivery_id AND a.callback_status=requested_callback_status;
        IF FOUND THEN
          IF prior.payload_digest<>digest_value THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='conflicting connector callback replay'; END IF;
          IF requested_callback_status='failed' AND EXISTS(SELECT 1 FROM campaign_delivery_callback_attempts a
            WHERE a.tenant_id=requested_tenant_id AND a.delivery_id=requested_delivery_id
              AND a.callback_status='success') THEN
            RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='successful callback settlement is final'; END IF;
          delivery_id:=delivery_row.id;claim_id:=delivery_row.claim_id;outbox_id:=delivery_row.campaign_outbox_id;
          current_status:=delivery_row.status;replayed:=true;settled_at:=prior.recorded_at;RETURN NEXT;RETURN;
        END IF;
        IF requested_callback_status='failed' AND EXISTS(SELECT 1 FROM campaign_delivery_callback_attempts a
          WHERE a.tenant_id=requested_tenant_id AND a.delivery_id=requested_delivery_id
            AND a.callback_status='success') THEN
          RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='successful callback settlement is final'; END IF;
        IF requested_callback_status='success' AND EXISTS(SELECT 1 FROM campaign_delivery_callback_attempts a
          WHERE a.tenant_id=requested_tenant_id AND a.delivery_id=requested_delivery_id
            AND a.callback_status='failed') THEN
          UPDATE benefit_deliveries SET external_data=COALESCE(external_data::jsonb,'{}'::jsonb)
              -'_callback_external_id'-'_callback_status'
          WHERE tenant_id=requested_tenant_id AND id=requested_delivery_id;
          UPDATE campaign_claim_outbox SET status='awaiting_callback',updated_at=CURRENT_TIMESTAMP
          WHERE tenant_id=requested_tenant_id AND id=delivery_row.campaign_outbox_id AND status='pending';
        END IF;
        SELECT * INTO settled FROM public.settle_campaign_claim_callback_legacy(requested_tenant_id,
          requested_audit_id,requested_connector_id,requested_delivery_id,requested_claim_id,
          requested_external_id,requested_callback_status,'{}'::jsonb);
        INSERT INTO campaign_delivery_callback_attempts(id,tenant_id,delivery_id,claim_id,callback_status,
          external_id,payload_digest,recorded_at) VALUES(requested_audit_id,
          requested_tenant_id,requested_delivery_id,requested_claim_id,requested_callback_status,
          trim(requested_external_id),digest_value,CURRENT_TIMESTAMP);
        delivery_id:=settled.delivery_id;claim_id:=settled.claim_id;outbox_id:=settled.outbox_id;
        current_status:=settled.current_status;replayed:=false;settled_at:=settled.settled_at;RETURN NEXT;
      EXCEPTION WHEN lock_not_available THEN
        RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='connector callback authority is busy';
      END $fn$
    """)
    for signature in (_CALLBACK, _LEGACY_CALLBACK):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists("yimatong_app"):
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
    for signature in (_DELIVERY_RESULT, _LEGACY_DELIVERY_RESULT):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists("yimatong_app"):
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
    if _role_exists("yimatong_app"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_DELIVERY_RESULT} TO yimatong_app")
    if _role_exists("yimatong_callback"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{_LEGACY_CALLBACK} FROM yimatong_callback")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_CALLBACK} TO yimatong_callback")
    for role in ("PUBLIC", "yimatong_app", "yimatong_callback"):
        if role == "PUBLIC" or _role_exists(role):
            op.execute(f"REVOKE ALL ON public.campaign_delivery_callback_attempts FROM {role}")


def downgrade() -> None:
    _downgrade_preflight()
    _prepare_downgrade_index()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_DELIVERY_RESULT}")
    op.execute(
        "ALTER FUNCTION public.record_campaign_claim_delivery_result_legacy"
        "(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer) RENAME TO record_campaign_claim_delivery_result"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{_CALLBACK}")
    op.execute(
        "ALTER FUNCTION public.settle_campaign_claim_callback_legacy(uuid,uuid,uuid,uuid,uuid,text,text,jsonb) "
        "RENAME TO settle_campaign_claim_callback"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_campaign_delivery_callback_attempts ON campaign_delivery_callback_attempts"
    )
    op.execute("DROP FUNCTION IF EXISTS public.guard_campaign_delivery_callback_attempts()")
    op.drop_index(
        "ix_campaign_delivery_callback_attempts_delivery_id", table_name="campaign_delivery_callback_attempts"
    )
    op.drop_index(
        "ix_campaign_delivery_callback_attempts_tenant_id", table_name="campaign_delivery_callback_attempts"
    )
    op.drop_index("ix_campaign_callback_attempts_delivery_time", table_name="campaign_delivery_callback_attempts")
    op.drop_table("campaign_delivery_callback_attempts")
    op.drop_constraint("uq_benefit_deliveries_tenant_id_id", "benefit_deliveries", type_="unique")
    op.execute(f"ALTER INDEX public.{_DOWNGRADE_INDEX} RENAME TO uq_benefit_deliveries_tenant_id_id")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CALLBACK} FROM PUBLIC")
    if _role_exists("yimatong_callback"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_CALLBACK} TO yimatong_callback")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_DELIVERY_RESULT} FROM PUBLIC")
    if _role_exists("yimatong_app"):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_DELIVERY_RESULT} TO yimatong_app")
