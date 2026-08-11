"""Expand campaign authority and durable claim outbox.

Revision ID: u6a0b1c2d3e4
Revises: u5a3d4e5f6a7
Create Date: 2026-08-11

This transactional stage adds nullable timestamp shadows plus an old-writer
compatibility trigger. It never rewrites the campaign table. Existing
timezone-less values are interpreted as Asia/Shanghai business time;
offset-bearing values retain their declared instant.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6a0b1c2d3e4"
down_revision: str | None = "u5a3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"


def _role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE}).scalar()
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute(
        r"""
        DO $block$
        DECLARE bad_id uuid;
        BEGIN
            BEGIN
                SELECT campaign.id INTO bad_id
                FROM public.campaigns AS campaign
                WHERE campaign.start_at IS NULL OR campaign.end_at IS NULL
                   OR (CASE
                        WHEN campaign.start_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
                        THEN campaign.start_at::timestamptz
                        ELSE campaign.start_at::timestamp AT TIME ZONE 'Asia/Shanghai'
                       END) >=
                      (CASE
                        WHEN campaign.end_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
                        THEN campaign.end_at::timestamptz
                        ELSE campaign.end_at::timestamp AT TIME ZONE 'Asia/Shanghai'
                       END)
                LIMIT 1;
            EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RAISE EXCEPTION USING ERRCODE='22007', MESSAGE='campaign timestamp conversion preflight failed';
            END;
            IF bad_id IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='campaign time window preflight failed',
                    DETAIL=format('campaign_id=%s',bad_id);
            END IF;
            IF EXISTS(SELECT 1 FROM public.campaigns WHERE status NOT IN ('draft','active','paused','ended')) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='campaign status preflight failed';
            END IF;
            IF EXISTS(
                SELECT 1 FROM public.benefits
                WHERE status NOT IN ('active','inactive')
                   OR benefit_type NOT IN (
                       'platform_coupon','external_link','private_domain','form_benefit','cash_red_packet'
                   )
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='benefit status/type preflight failed';
            END IF;
            IF EXISTS(
                SELECT 1 FROM public.benefit_claims
                WHERE claim_type<>'claim' OR status NOT IN ('success','claimed','delivered','used','failed')
                   OR delivery_status NOT IN (
                       'not_required','pending','processing','success','delivered','failed','dead_letter'
                   )
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='benefit claim state preflight failed';
            END IF;
        END
        $block$
        """
    )
    op.add_column("campaigns", sa.Column("start_at_tz", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaigns", sa.Column("end_at_tz", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        r"""
        CREATE FUNCTION public.populate_campaign_time_shadows() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF NEW.start_at IS NULL OR NEW.end_at IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='23502',MESSAGE='campaign time window is required';
            END IF;
            BEGIN
                NEW.start_at_tz:=CASE WHEN NEW.start_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
                    THEN NEW.start_at::timestamptz
                    ELSE NEW.start_at::timestamp AT TIME ZONE 'Asia/Shanghai' END;
                NEW.end_at_tz:=CASE WHEN NEW.end_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
                    THEN NEW.end_at::timestamptz
                    ELSE NEW.end_at::timestamp AT TIME ZONE 'Asia/Shanghai' END;
            EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RAISE EXCEPTION USING ERRCODE='22007',MESSAGE='campaign timestamp conversion failed';
            END;
            IF NEW.end_at_tz<=NEW.start_at_tz THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign time window invalid';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_campaign_time_shadows() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_campaign_time_shadows BEFORE INSERT OR UPDATE OF start_at,end_at "
        "ON public.campaigns FOR EACH ROW EXECUTE FUNCTION public.populate_campaign_time_shadows()"
    )
    for statement in (
        "ALTER TABLE public.campaigns ADD CONSTRAINT ck_campaigns_status "
        "CHECK (status IN ('draft','active','paused','ended')) NOT VALID",
        "ALTER TABLE public.benefits ADD CONSTRAINT ck_benefits_status "
        "CHECK (status IN ('active','inactive')) NOT VALID",
        "ALTER TABLE public.benefits ADD CONSTRAINT ck_benefits_type CHECK (benefit_type IN "
        "('platform_coupon','external_link','private_domain','form_benefit','cash_red_packet')) NOT VALID",
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT ck_benefit_claims_type "
        "CHECK (claim_type='claim') NOT VALID",
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT ck_benefit_claims_status "
        "CHECK (status IN ('success','claimed','delivered','used','failed')) NOT VALID",
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT ck_benefit_claims_delivery_status CHECK (delivery_status IN "
        "('not_required','pending','processing','success','delivered','failed','dead_letter')) NOT VALID",
    ):
        op.execute(statement)
    for table, constraint in (
        ("campaigns", "ck_campaigns_status"),
        ("benefits", "ck_benefits_status"),
        ("benefits", "ck_benefits_type"),
        ("benefit_claims", "ck_benefit_claims_type"),
        ("benefit_claims", "ck_benefit_claims_status"),
        ("benefit_claims", "ck_benefit_claims_delivery_status"),
    ):
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {constraint}")

    op.add_column("benefit_claims", sa.Column("reserved_amount", sa.Integer(), nullable=True))
    op.add_column(
        "benefit_claims",
        sa.Column("reservation_status", sa.String(20), nullable=False, server_default="not_required"),
    )
    op.execute(
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT ck_benefit_claims_reservation CHECK ("
        "(reservation_status='not_required' AND reserved_amount IS NULL) OR "
        "(reservation_status IN ('reserved','settled','refunded') AND reserved_amount>0)) NOT VALID"
    )
    op.execute("ALTER TABLE public.benefit_claims VALIDATE CONSTRAINT ck_benefit_claims_reservation")

    op.add_column("benefit_deliveries", sa.Column("campaign_outbox_id", sa.Uuid(), nullable=True))
    op.add_column("benefit_deliveries", sa.Column("external_id", sa.String(200), nullable=True))
    op.execute(
        "ALTER TABLE public.benefit_deliveries ADD CONSTRAINT ck_benefit_deliveries_authority_identity CHECK ("
        "campaign_outbox_id IS NULL OR (claim_id IS NOT NULL AND benefit_id IS NOT NULL "
        "AND NULLIF(trim(external_id),'') IS NOT NULL)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.benefit_deliveries VALIDATE CONSTRAINT ck_benefit_deliveries_authority_identity"
    )

    op.create_table(
        "campaign_claim_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("last_lease_token", sa.Uuid(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("event_type='claim_committed'", name="ck_campaign_claim_outbox_event_type"),
        sa.CheckConstraint(
            "status IN ('pending','processing','awaiting_callback','delivered','dead_letter')",
            name="ck_campaign_claim_outbox_status",
        ),
        sa.CheckConstraint("attempt_count>=0 AND max_attempts>=1", name="ck_campaign_claim_outbox_attempts"),
        sa.CheckConstraint(
            "(status='processing' AND lease_token IS NOT NULL AND leased_until IS NOT NULL "
            "AND NULLIF(trim(worker_id),'') IS NOT NULL) OR "
            "(status<>'processing' AND lease_token IS NULL AND leased_until IS NULL AND worker_id IS NULL)",
            name="ck_campaign_claim_outbox_lease",
        ),
        sa.CheckConstraint(
            "(status='delivered' AND delivered_at IS NOT NULL) OR "
            "(status<>'delivered' AND delivered_at IS NULL)",
            name="ck_campaign_claim_outbox_delivery",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_campaign_claim_outbox_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "claim_id", "event_type", name="uq_campaign_claim_outbox_claim_event"),
    )
    op.create_index("ix_campaign_claim_outbox_tenant_status", "campaign_claim_outbox", ["tenant_id", "status"])
    op.create_index(
        "ix_campaign_claim_outbox_ready",
        "campaign_claim_outbox",
        ["tenant_id", "next_attempt_at", "id"],
        postgresql_where=sa.text("status IN ('pending','awaiting_callback')"),
    )
    op.create_index(
        "ix_campaign_claim_outbox_expired_lease",
        "campaign_claim_outbox",
        ["tenant_id", "leased_until", "id"],
        postgresql_where=sa.text("status='processing'"),
    )
    op.execute("ALTER TABLE public.campaign_claim_outbox ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.campaign_claim_outbox FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.campaign_claim_outbox
        USING (
            tenant_id=public.current_tenant_id()
            OR (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true'
                AND has_parameter_privilege(current_user,'app.bypass_rls','SET'))
        )
        WITH CHECK (
            tenant_id=public.current_tenant_id()
            OR (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true'
                AND has_parameter_privilege(current_user,'app.bypass_rls','SET'))
        )
        """
    )
    op.execute("REVOKE ALL ON TABLE public.campaign_claim_outbox FROM PUBLIC")
    if _role_exists():
        op.execute(f"REVOKE ALL ON TABLE public.campaign_claim_outbox FROM {_RUNTIME_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM public.campaign_claim_outbox)")).scalar():
        raise RuntimeError("campaign claim outbox facts block downgrade")
    op.execute("DROP POLICY tenant_isolation ON public.campaign_claim_outbox")
    op.drop_table("campaign_claim_outbox")
    op.drop_constraint("ck_benefit_deliveries_authority_identity", "benefit_deliveries", type_="check")
    op.drop_column("benefit_deliveries", "external_id")
    op.drop_column("benefit_deliveries", "campaign_outbox_id")
    if op.get_bind().execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM public.benefit_claims WHERE reservation_status<>'not_required')")
    ).scalar():
        raise RuntimeError("benefit claim reservation facts block downgrade")
    op.drop_constraint("ck_benefit_claims_reservation", "benefit_claims", type_="check")
    op.drop_column("benefit_claims", "reservation_status")
    op.drop_column("benefit_claims", "reserved_amount")
    for table, constraint in reversed(
        (
            ("campaigns", "ck_campaigns_status"),
            ("benefits", "ck_benefits_status"),
            ("benefits", "ck_benefits_type"),
            ("benefit_claims", "ck_benefit_claims_type"),
            ("benefit_claims", "ck_benefit_claims_status"),
            ("benefit_claims", "ck_benefit_claims_delivery_status"),
        )
    ):
        op.drop_constraint(constraint, table, type_="check")
    op.execute("DROP TRIGGER trg_campaign_time_shadows ON public.campaigns")
    op.execute("DROP FUNCTION public.populate_campaign_time_shadows()")
    op.drop_column("campaigns", "end_at_tz")
    op.drop_column("campaigns", "start_at_tz")
