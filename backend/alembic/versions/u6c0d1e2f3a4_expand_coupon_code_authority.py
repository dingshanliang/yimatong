"""Expand coupon-code tenant and claim authority.

Revision ID: u6c0d1e2f3a4
Revises: u6b3f4a5b6c7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6c0d1e2f3a4"
down_revision: str | None = "u6b3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_pool_derived_policy() -> None:
    derived = (
        "EXISTS (SELECT 1 FROM public.coupon_pools AS owner WHERE owner.id=coupon_codes.pool_id "
        "AND owner.tenant_id=public.current_tenant_id())"
    )
    bypass = (
        "(public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true' "
        "AND has_parameter_privilege(session_user,'app.bypass_rls','SET'))"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.coupon_codes")
    op.execute("ALTER TABLE public.coupon_codes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.coupon_codes FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON public.coupon_codes "
        f"USING (({derived}) OR {bypass}) WITH CHECK (({derived}) OR {bypass})"
    )


def _orphan_preflight(table: str) -> None:
    op.execute(
        f"""
        DO $block$
        DECLARE orphan_ids text;
        BEGIN
            SELECT string_agg(child.id::text,', ' ORDER BY child.id::text) INTO orphan_ids
            FROM public.{table} AS child
            LEFT JOIN public.tenants AS tenant ON tenant.id=child.tenant_id
            WHERE tenant.id IS NULL;
            IF orphan_ids IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23503',
                    MESSAGE='{table} contains orphan tenant ids: '||orphan_ids;
            END IF;
        END
        $block$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    _orphan_preflight("consumer_profiles")
    _orphan_preflight("coupon_pools")
    op.create_foreign_key(
        "fk_consumer_profiles_tenant",
        "consumer_profiles",
        "tenants",
        ["tenant_id"],
        ["id"],
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_coupon_pools_tenant",
        "coupon_pools",
        "tenants",
        ["tenant_id"],
        ["id"],
        postgresql_not_valid=True,
    )
    op.add_column("coupon_codes", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.add_column("coupon_codes", sa.Column("claim_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        CREATE FUNCTION public.populate_coupon_code_tenant()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE resolved_tenant uuid;
        BEGIN
            SELECT pool.tenant_id INTO resolved_tenant
            FROM public.coupon_pools AS pool WHERE pool.id=NEW.pool_id FOR KEY SHARE;
            IF resolved_tenant IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon pool is unavailable';
            END IF;
            IF NEW.tenant_id IS NULL THEN NEW.tenant_id:=resolved_tenant; END IF;
            IF NEW.tenant_id IS DISTINCT FROM resolved_tenant THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon code tenant does not own pool';
            END IF;
            IF TG_OP='UPDATE' AND OLD.tenant_id IS NOT NULL
               AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='coupon code tenant is immutable';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_coupon_code_tenant() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_populate_coupon_code_tenant "
        "BEFORE INSERT OR UPDATE OF tenant_id,pool_id ON public.coupon_codes "
        "FOR EACH ROW EXECUTE FUNCTION public.populate_coupon_code_tenant()"
    )
    _install_pool_derived_policy()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        """
        DO $block$
        DECLARE bound_ids text;
        BEGIN
            SELECT string_agg(id::text,', ' ORDER BY id::text) INTO bound_ids
            FROM public.coupon_codes WHERE claim_id IS NOT NULL;
            IF bound_ids IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard coupon claim bindings; coupon code ids: '||bound_ids;
            END IF;
        END
        $block$
        """
    )
    op.execute("DROP TRIGGER trg_populate_coupon_code_tenant ON public.coupon_codes")
    op.execute("DROP FUNCTION public.populate_coupon_code_tenant()")
    op.drop_column("coupon_codes", "claim_id")
    op.drop_column("coupon_codes", "tenant_id")
    op.drop_constraint("fk_coupon_pools_tenant", "coupon_pools", type_="foreignkey")
    op.drop_constraint("fk_consumer_profiles_tenant", "consumer_profiles", type_="foreignkey")
