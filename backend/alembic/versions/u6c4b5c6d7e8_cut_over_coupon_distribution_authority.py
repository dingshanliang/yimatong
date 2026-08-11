"""Cut over coupon distribution to tenant-bound database authority.

Revision ID: u6c4b5c6d7e8
Revises: u6c3a4b5c6d7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6c4b5c6d7e8"
down_revision: str | None = "u6c3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNATURE = "allocate_coupon_code(uuid,uuid,text,uuid)"


def _role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar()
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.coupon_codes")
    op.execute("ALTER TABLE public.coupon_codes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.coupon_codes FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.coupon_codes
        USING (
            tenant_id=public.current_tenant_id() OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls',true)='true'
                AND has_parameter_privilege(session_user,'app.bypass_rls','SET')
            )
        )
        WITH CHECK (
            tenant_id=public.current_tenant_id() OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls',true)='true'
                AND has_parameter_privilege(session_user,'app.bypass_rls','SET')
            )
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.allocate_coupon_code(
            requested_tenant_id uuid,requested_pool_id uuid,requested_consumer_id text,requested_claim_id uuid
        ) RETURNS TABLE(coupon_code_id uuid,code text,remaining integer,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE pool_row record; DECLARE claim_row record; DECLARE code_row record;
        DECLARE remaining_count integer;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon allocation tenant context mismatch';
            END IF;
            IF requested_pool_id IS NULL OR NULLIF(trim(requested_consumer_id),'') IS NULL
               OR length(requested_consumer_id)>100 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='coupon allocation input is invalid';
            END IF;
            SELECT pool.id,pool.remaining INTO pool_row FROM public.coupon_pools AS pool
            WHERE pool.tenant_id=requested_tenant_id AND pool.id=requested_pool_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon pool is unavailable'; END IF;
            IF requested_claim_id IS NOT NULL THEN
                PERFORM pg_advisory_xact_lock(hashtextextended('coupon-claim:'||requested_claim_id::text,0));
                SELECT claim.consumer_id,claim.status INTO claim_row FROM public.benefit_claims AS claim
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon claim is unavailable';
                END IF;
                IF claim_row.consumer_id IS DISTINCT FROM trim(requested_consumer_id)
                   OR claim_row.status NOT IN ('success','claimed','delivered','used') THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon claim does not match consumer or state';
                END IF;
                SELECT item.id,item.code,item.pool_id INTO code_row FROM public.coupon_codes AS item
                WHERE item.tenant_id=requested_tenant_id AND item.claim_id=requested_claim_id FOR UPDATE;
                IF FOUND THEN
                    IF code_row.pool_id IS DISTINCT FROM requested_pool_id THEN
                        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon claim is bound to another pool';
                    END IF;
                    coupon_code_id:=code_row.id; code:=code_row.code; remaining:=pool_row.remaining;
                    replayed:=true; RETURN NEXT; RETURN;
                END IF;
            END IF;
            SELECT item.id,item.code INTO code_row FROM public.coupon_codes AS item
            WHERE item.tenant_id=requested_tenant_id AND item.pool_id=requested_pool_id
              AND NOT item.distributed AND item.claim_id IS NULL
            ORDER BY item.id LIMIT 1 FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon pool is exhausted'; END IF;
            UPDATE public.coupon_codes AS item SET distributed=true,consumer_id=trim(requested_consumer_id),
                claim_id=requested_claim_id,updated_at=CURRENT_TIMESTAMP
            WHERE item.tenant_id=requested_tenant_id AND item.id=code_row.id;
            SELECT count(*)::integer INTO remaining_count FROM public.coupon_codes AS item
            WHERE item.tenant_id=requested_tenant_id AND item.pool_id=requested_pool_id AND NOT item.distributed;
            UPDATE public.coupon_pools AS pool SET remaining=remaining_count,updated_at=CURRENT_TIMESTAMP
            WHERE pool.tenant_id=requested_tenant_id AND pool.id=requested_pool_id;
            coupon_code_id:=code_row.id; code:=code_row.code; remaining:=remaining_count;
            replayed:=false; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='coupon pool is busy';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_SIGNATURE} TO yimatong_app")
        op.execute("REVOKE UPDATE,DELETE ON public.coupon_codes FROM yimatong_app")
        op.execute("REVOKE UPDATE ON public.coupon_pools FROM yimatong_app")
        op.execute("GRANT UPDATE(name,updated_at) ON public.coupon_pools TO yimatong_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP FUNCTION public.{_SIGNATURE}")
    op.execute("DROP POLICY tenant_isolation ON public.coupon_codes")
    derived = (
        "EXISTS (SELECT 1 FROM public.coupon_pools AS owner WHERE owner.id=coupon_codes.pool_id "
        "AND owner.tenant_id=public.current_tenant_id())"
    )
    bypass = (
        "(public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true' "
        "AND has_parameter_privilege(session_user,'app.bypass_rls','SET'))"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation ON public.coupon_codes "
        f"USING (({derived}) OR {bypass}) WITH CHECK (({derived}) OR {bypass})"
    )
    if _role_exists():
        op.execute("GRANT UPDATE,DELETE ON public.coupon_codes TO yimatong_app")
        op.execute("GRANT UPDATE ON public.coupon_pools TO yimatong_app")
