"""expand external order value ledger

Revision ID: u8a0c1d2e3f4
Revises: u6l4a5b6c7d8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8a0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u6l4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for name in ("ledger_original_amount", "ledger_refunded_amount", "ledger_cancelled_amount", "ledger_net_amount"):
        op.add_column("external_orders", sa.Column(name, sa.Numeric(20, 6), nullable=True))
    op.add_column("external_orders", sa.Column("ledger_status", sa.String(30), nullable=True))

    op.create_table(
        "external_order_value_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("external_order_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("result_original_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("result_refunded_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("result_cancelled_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("result_net_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("result_status", sa.String(30), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("statement_timestamp()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_external_order_receipt_payload_digest_u8a"),
        sa.CheckConstraint("event_type IN ('order_confirmed','refund','cancel')", name="ck_external_order_receipt_type_u8a"),
        sa.CheckConstraint("result_original_amount > 0 AND result_refunded_amount >= 0 AND result_cancelled_amount >= 0 AND result_net_amount >= 0", name="ck_external_order_receipt_amounts_u8a"),
        sa.CheckConstraint("result_refunded_amount + result_cancelled_amount + result_net_amount = result_original_amount", name="ck_external_order_receipt_balance_u8a"),
    )
    op.create_table(
        "external_order_value_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("event_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("external_order_id", sa.String(100), nullable=False),
        sa.Column("provenance_type", sa.String(30), nullable=False),
        sa.Column("provenance_digest", sa.String(64), nullable=False),
        sa.Column("provenance_verified", sa.Boolean(), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("statement_timestamp()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["receipt_id"], ["external_order_value_receipts.id"], name="fk_external_order_events_receipt_u8a"),
        sa.CheckConstraint("sequence_no > 0", name="ck_external_order_events_sequence_u8a"),
        sa.CheckConstraint("event_type IN ('order_confirmed','refund','cancel')", name="ck_external_order_events_type_u8a"),
        sa.CheckConstraint("event_amount >= 0 AND (event_type <> 'order_confirmed' OR event_amount > 0)", name="ck_external_order_events_amount_u8a"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_external_order_events_currency_u8a"),
        sa.CheckConstraint("provenance_digest ~ '^[0-9a-f]{64}$'", name="ck_external_order_events_provenance_u8a"),
        sa.CheckConstraint("provenance_type IN ('manual_import','verified_callback','backfill')", name="ck_external_order_events_provenance_type_u8a"),
        sa.CheckConstraint("provenance_verified = (provenance_type = 'verified_callback')", name="ck_external_order_events_verified_u8a"),
        sa.CheckConstraint("actor_type IN ('account','api_key','connector','system','migration')", name="ck_external_order_events_actor_type_u8a"),
        sa.CheckConstraint("actor_id IS NOT NULL OR actor_type IN ('system','migration')", name="ck_external_order_events_actor_u8a"),
    )
    op.create_table(
        "external_order_ledger_recovery_markers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("statement_timestamp()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_external_order_recovery_order_u8a"),
        sa.CheckConstraint("snapshot_digest ~ '^[0-9a-f]{64}$'", name="ck_external_order_recovery_digest_u8a"),
    )
    for table in ("external_order_value_receipts", "external_order_value_events", "external_order_ledger_recovery_markers"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} USING (tenant_id=public.current_tenant_id() OR "
            "(public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true' "
            "AND has_parameter_privilege(current_user,'app.bypass_rls','SET'))) WITH CHECK (tenant_id=public.current_tenant_id() OR "
            "(public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true' "
            "AND has_parameter_privilege(current_user,'app.bypass_rls','SET')))"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC")
        op.execute(
            f"DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM yimatong_app; END IF; END $do$"
        )
    # Coordinate legacy writes with the online index phase. Each statement on
    # the only populated target takes a shared transaction advisory lock;
    # u8a1 takes the matching exclusive session lock before publishing any CIC
    # catalog state. Unrelated relations never participate in this protocol.
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


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    facts = op.get_bind().execute(sa.text("SELECT (SELECT count(*) FROM external_order_value_events) + (SELECT count(*) FROM external_order_value_receipts)")).scalar_one()
    if facts:
        raise RuntimeError("u8a0 downgrade blocked: immutable external order ledger facts exist")
    op.execute("DROP TRIGGER IF EXISTS trg_coordinate_external_order_index_build_u8a ON public.external_orders")
    op.execute("DROP FUNCTION IF EXISTS public.coordinate_external_order_index_build_u8a()")
    for table in ("external_order_ledger_recovery_markers", "external_order_value_events", "external_order_value_receipts"):
        op.drop_table(table)
    for name in ("ledger_status", "ledger_net_amount", "ledger_cancelled_amount", "ledger_refunded_amount", "ledger_original_amount"):
        op.drop_column("external_orders", name)
