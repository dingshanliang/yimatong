"""Preserve coupon attribution after a full refund restores the coupon.

Revision ID: 5e3f9a4bac21
Revises: 0b91acfedc87
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5e3f9a4bac21"
down_revision: str | Sequence[str] | None = "0b91acfedc87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUPON_ORDER_REF = "NULLIF(event_data->>'coupon_order_ref','')"
_OLD_SCOPE = "status='used' AND used_order_ref=event_data->>'order_ref'"
_NEW_SCOPE = (
    "EXISTS(SELECT 1 FROM public.repurchase_coupon_events coupon_event "
    "WHERE coupon_event.tenant_id=requested_tenant_id "
    "AND coupon_event.coupon_id=coupon_id_value "
    "AND coupon_event.event_type='committed' "
    f"AND ({_COUPON_ORDER_REF} IS NULL OR coupon_event.order_ref={_COUPON_ORDER_REF}))"
)


def _replace_scope(old: str, new: str) -> None:
    escaped_old = old.replace("'", "''")
    escaped_new = new.replace("'", "''")
    op.execute(
        f"""
        DO $migration$
        DECLARE current_definition text; patched_definition text;
        BEGIN
          SELECT pg_get_functiondef(
            'public.project_commerce_order_event_authority(uuid,uuid)'::regprocedure
          ) INTO current_definition;
          patched_definition := replace(current_definition, '{escaped_old}', '{escaped_new}');
          IF patched_definition=current_definition THEN
            RAISE EXCEPTION 'commerce order projector coupon history clause not found';
          END IF;
          EXECUTE patched_definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_commerce_repurchase_attributions_coupon "
        "ON public.commerce_repurchase_attributions(tenant_id,coupon_id) "
        "WHERE coupon_id IS NOT NULL"
    )
    _replace_scope(_OLD_SCOPE, _NEW_SCOPE)


def downgrade() -> None:
    op.execute(
        """DO $guard$ BEGIN
          IF EXISTS(SELECT 1 FROM public.member_notification_deliveries WHERE status='delivering') THEN
            RAISE EXCEPTION 'notification deliveries are in flight; wait for leases before downgrade';
          END IF;
          IF EXISTS(SELECT 1 FROM public.commerce_repurchase_attributions WHERE coupon_id IS NOT NULL) THEN
            RAISE EXCEPTION 'commerce coupon attribution facts exist; archive before downgrade';
          END IF;
        END $guard$"""
    )
    _replace_scope(_NEW_SCOPE, _OLD_SCOPE)
    op.execute("DROP INDEX public.uq_commerce_repurchase_attributions_coupon")
