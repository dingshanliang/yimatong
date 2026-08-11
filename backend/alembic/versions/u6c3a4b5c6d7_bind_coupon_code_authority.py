"""Bind coupon-code tenant and claim integrity.

Revision ID: u6c3a4b5c6d7
Revises: u6c2f3a4b5c6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u6c3a4b5c6d7"
down_revision: str | None = "u6c2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("LOCK TABLE public.coupon_codes,public.coupon_pools IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        DO $block$
        DECLARE invalid_ids text;
        BEGIN
            SELECT string_agg(code.id::text,', ' ORDER BY code.id::text) INTO invalid_ids
            FROM public.coupon_codes AS code
            LEFT JOIN public.coupon_pools AS pool
              ON pool.tenant_id=code.tenant_id AND pool.id=code.pool_id
            WHERE code.tenant_id IS NULL OR pool.id IS NULL;
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23503',
                    MESSAGE='coupon codes lack an authoritative tenant pool: '||invalid_ids;
            END IF;
        END
        $block$
        """
    )
    op.execute(
        "UPDATE public.coupon_pools AS pool SET "
        "total_codes=(SELECT count(*) FROM public.coupon_codes AS code "
        "WHERE code.tenant_id=pool.tenant_id AND code.pool_id=pool.id),"
        "remaining=(SELECT count(*) FROM public.coupon_codes AS code "
        "WHERE code.tenant_id=pool.tenant_id AND code.pool_id=pool.id AND NOT code.distributed),"
        "updated_at=CURRENT_TIMESTAMP"
    )
    op.create_foreign_key(
        "fk_coupon_codes_tenant_pool",
        "coupon_codes",
        "coupon_pools",
        ["tenant_id", "pool_id"],
        ["tenant_id", "id"],
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_coupon_codes_tenant_claim",
        "coupon_codes",
        "benefit_claims",
        ["tenant_id", "claim_id"],
        ["tenant_id", "id"],
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_coupon_codes_claim_distribution",
        "coupon_codes",
        "claim_id IS NULL OR (distributed IS TRUE AND NULLIF(trim(consumer_id),'') IS NOT NULL)",
    )
    op.create_check_constraint("ck_coupon_pools_total_nonnegative", "coupon_pools", "total_codes >= 0")
    op.create_check_constraint(
        "ck_coupon_pools_remaining_range",
        "coupon_pools",
        "remaining >= 0 AND remaining <= total_codes",
    )
    for table, constraint in (
        ("consumer_profiles", "fk_consumer_profiles_tenant"),
        ("coupon_pools", "fk_coupon_pools_tenant"),
        ("coupon_codes", "fk_coupon_codes_tenant_pool"),
        ("coupon_codes", "fk_coupon_codes_tenant_claim"),
    ):
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {constraint}")
    op.alter_column("coupon_codes", "tenant_id", nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.alter_column("coupon_codes", "tenant_id", nullable=True)
    op.drop_constraint("ck_coupon_pools_remaining_range", "coupon_pools", type_="check")
    op.drop_constraint("ck_coupon_pools_total_nonnegative", "coupon_pools", type_="check")
    op.drop_constraint("ck_coupon_codes_claim_distribution", "coupon_codes", type_="check")
    op.drop_constraint("fk_coupon_codes_tenant_claim", "coupon_codes", type_="foreignkey")
    op.drop_constraint("fk_coupon_codes_tenant_pool", "coupon_codes", type_="foreignkey")
