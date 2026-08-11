"""Bind campaign, benefit, claim and outbox facts to tenant parents.

Revision ID: u6a2c3d4e5f6
Revises: u6ab1c2d3e4f
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6a2c3d4e5f6"
down_revision: str | None = "u6ab1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight() -> None:
    checks = {
        "campaign product tenant mismatch": """
            SELECT 1 FROM public.campaigns AS campaign
            LEFT JOIN public.products AS product ON product.id=campaign.product_id
            WHERE campaign.product_id IS NOT NULL
              AND (product.id IS NULL OR product.tenant_id<>campaign.tenant_id) LIMIT 1
        """,
        "benefit campaign tenant mismatch": """
            SELECT 1 FROM public.benefits AS benefit
            LEFT JOIN public.campaigns AS campaign ON campaign.id=benefit.campaign_id
            WHERE benefit.campaign_id IS NOT NULL
              AND (campaign.id IS NULL OR campaign.tenant_id<>benefit.tenant_id) LIMIT 1
        """,
        "claim benefit tenant mismatch": """
            SELECT 1 FROM public.benefit_claims AS claim
            LEFT JOIN public.benefits AS benefit ON benefit.id=claim.benefit_id
            WHERE benefit.id IS NULL OR benefit.tenant_id<>claim.tenant_id LIMIT 1
        """,
        "claim campaign tenant mismatch": """
            SELECT 1 FROM public.benefit_claims AS claim
            LEFT JOIN public.campaigns AS campaign ON campaign.id=claim.campaign_id
            WHERE claim.campaign_id IS NOT NULL
              AND (campaign.id IS NULL OR campaign.tenant_id<>claim.tenant_id) LIMIT 1
        """,
        "claim campaign snapshot differs from benefit attachment": """
            SELECT 1 FROM public.benefit_claims AS claim
            JOIN public.benefits AS benefit ON benefit.id=claim.benefit_id
            WHERE claim.campaign_id IS DISTINCT FROM benefit.campaign_id LIMIT 1
        """,
        "authoritative delivery outbox/claim tenant mismatch": """
            SELECT 1 FROM public.benefit_deliveries AS delivery
            LEFT JOIN public.campaign_claim_outbox AS box
              ON box.id=delivery.campaign_outbox_id
            LEFT JOIN public.benefit_claims AS claim ON claim.id=delivery.claim_id
            WHERE delivery.campaign_outbox_id IS NOT NULL
              AND (box.id IS NULL OR box.tenant_id<>delivery.tenant_id
                OR claim.id IS NULL OR claim.tenant_id<>delivery.tenant_id
                OR box.claim_id<>delivery.claim_id)
            LIMIT 1
        """,
    }
    for message, query in checks.items():
        if op.get_bind().execute(sa.text(query)).first() is not None:
            raise RuntimeError(message)


def _install_campaign_time_compatibility() -> None:
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


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    _preflight()
    op.execute(
        "ALTER TABLE public.campaigns ADD CONSTRAINT ck_campaigns_time_shadows_nn "
        "CHECK (start_at_tz IS NOT NULL AND end_at_tz IS NOT NULL) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.campaigns ADD CONSTRAINT ck_campaigns_time_window "
        "CHECK (end_at_tz>start_at_tz) NOT VALID"
    )
    op.execute("ALTER TABLE public.campaigns VALIDATE CONSTRAINT ck_campaigns_time_shadows_nn")
    op.execute("ALTER TABLE public.campaigns VALIDATE CONSTRAINT ck_campaigns_time_window")
    op.execute("DROP TRIGGER trg_campaign_time_shadows ON public.campaigns")
    op.execute("DROP FUNCTION public.populate_campaign_time_shadows()")
    op.alter_column("campaigns", "start_at", new_column_name="start_at_legacy")
    op.alter_column("campaigns", "end_at", new_column_name="end_at_legacy")
    op.alter_column("campaigns", "start_at_tz", new_column_name="start_at")
    op.alter_column("campaigns", "end_at_tz", new_column_name="end_at")
    op.alter_column("campaigns", "start_at", nullable=False)
    op.alter_column("campaigns", "end_at", nullable=False)
    op.drop_constraint("ck_campaigns_time_shadows_nn", "campaigns", type_="check")
    op.drop_column("campaigns", "end_at_legacy")
    op.drop_column("campaigns", "start_at_legacy")
    op.execute(
        "ALTER TABLE public.campaigns ADD CONSTRAINT uq_campaigns_tenant_id_id "
        "UNIQUE USING INDEX uq_campaigns_tenant_id_id"
    )
    op.execute(
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT uq_benefit_claims_tenant_id_id "
        "UNIQUE USING INDEX uq_benefit_claims_tenant_id_id"
    )
    op.execute(
        "ALTER TABLE public.benefit_claims ADD CONSTRAINT uq_benefit_claims_tenant_idempotent "
        "UNIQUE USING INDEX uq_benefit_claims_tenant_idempotent"
    )
    op.execute(
        "ALTER TABLE public.benefit_deliveries ADD CONSTRAINT uq_benefit_deliveries_tenant_outbox "
        "UNIQUE USING INDEX uq_benefit_deliveries_tenant_outbox"
    )
    constraints = (
        (
            "campaigns",
            "fk_campaigns_tenant_product",
            "FOREIGN KEY (tenant_id,product_id) REFERENCES public.products(tenant_id,id)",
        ),
        (
            "benefits",
            "fk_benefits_tenant_campaign",
            "FOREIGN KEY (tenant_id,campaign_id) REFERENCES public.campaigns(tenant_id,id)",
        ),
        (
            "benefit_claims",
            "fk_benefit_claims_tenant_benefit",
            "FOREIGN KEY (tenant_id,benefit_id) REFERENCES public.benefits(tenant_id,id)",
        ),
        (
            "benefit_claims",
            "fk_benefit_claims_tenant_campaign",
            "FOREIGN KEY (tenant_id,campaign_id) REFERENCES public.campaigns(tenant_id,id)",
        ),
        (
            "campaign_claim_outbox",
            "fk_campaign_claim_outbox_tenant_claim",
            "FOREIGN KEY (tenant_id,claim_id) REFERENCES public.benefit_claims(tenant_id,id) ON DELETE RESTRICT",
        ),
        (
            "benefit_deliveries",
            "fk_benefit_deliveries_tenant_claim",
            "FOREIGN KEY (tenant_id,claim_id) REFERENCES public.benefit_claims(tenant_id,id)",
        ),
        (
            "benefit_deliveries",
            "fk_benefit_deliveries_tenant_outbox",
            "FOREIGN KEY (tenant_id,campaign_outbox_id) "
            "REFERENCES public.campaign_claim_outbox(tenant_id,id)",
        ),
    )
    for table, name, definition in constraints:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} {definition} NOT VALID")
    for table, name, _definition in constraints:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.drop_constraint("ck_campaigns_time_window", "campaigns", type_="check")
    op.alter_column("campaigns", "start_at", new_column_name="start_at_tz")
    op.alter_column("campaigns", "end_at", new_column_name="end_at_tz")
    op.add_column("campaigns", sa.Column("start_at", sa.String(30), nullable=True))
    op.add_column("campaigns", sa.Column("end_at", sa.String(30), nullable=True))
    op.execute(
        "UPDATE public.campaigns SET start_at=start_at_tz::text,end_at=end_at_tz::text "
        "WHERE start_at IS NULL OR end_at IS NULL"
    )
    op.alter_column("campaigns", "start_at", nullable=False)
    op.alter_column("campaigns", "end_at", nullable=False)
    _install_campaign_time_compatibility()
    for table, name in reversed(
        (
            ("campaigns", "fk_campaigns_tenant_product"),
            ("benefits", "fk_benefits_tenant_campaign"),
            ("benefit_claims", "fk_benefit_claims_tenant_benefit"),
            ("benefit_claims", "fk_benefit_claims_tenant_campaign"),
            ("campaign_claim_outbox", "fk_campaign_claim_outbox_tenant_claim"),
            ("benefit_deliveries", "fk_benefit_deliveries_tenant_claim"),
            ("benefit_deliveries", "fk_benefit_deliveries_tenant_outbox"),
        )
    ):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint("uq_benefit_claims_tenant_idempotent", "benefit_claims", type_="unique")
    op.drop_constraint("uq_benefit_deliveries_tenant_outbox", "benefit_deliveries", type_="unique")
    op.drop_constraint("uq_benefit_claims_tenant_id_id", "benefit_claims", type_="unique")
    op.drop_constraint("uq_campaigns_tenant_id_id", "campaigns", type_="unique")
