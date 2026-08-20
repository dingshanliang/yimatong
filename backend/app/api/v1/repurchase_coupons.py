"""Admin, consumer-wallet, and authorized-store coupon surfaces."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.consumers import VerifiedConsumerScanContext, _resolve_scan_context, verify_consumer_scan_request
from app.core.database import get_db, get_db_for_consumer
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.repurchase_coupon import (
    CouponIssueRequest,
    CouponItem,
    CouponOrderTransition,
    CouponReserveRequest,
    CouponReverseRequest,
    CouponRevokeRequest,
    CouponRuleCreate,
    CouponRuleTransition,
    StoreRedemptionRequest,
    StoreRedemptionTokenResponse,
)
from app.services.brand_membership import get_brand_membership_for_profile
from app.services.channel import get_account_scope
from app.services.channel_access import require_brand_channel_principal, require_store_portal_principal
from app.services.repurchase_coupon import (
    commit_member_coupon,
    create_coupon_rule,
    decode_store_redemption_token,
    get_coupon_rule,
    get_member_coupon,
    issue_member_coupon,
    issue_store_redemption_token,
    list_member_wallet,
    redeem_member_coupon_at_store,
    release_member_coupon,
    reserve_member_coupon,
    reverse_member_coupon,
    revoke_member_coupon,
    transition_coupon_rule,
)
from app.utils import utcnow
from app.utils.auth_rbac import require_permission

repurchase_coupon_router = APIRouter(prefix="/api/v1", tags=["repurchase-coupons"])
_ADMIN_WRITE = [Depends(require_brand_channel_principal), Depends(require_permission("campaign:write"))]


def _rule_dict(rule) -> dict:
    return {
        "id": rule.id,
        "rule_key": rule.rule_key,
        "version": rule.version,
        "benefit_id": rule.benefit_id,
        "name": rule.name,
        "amount_minor": rule.amount_minor,
        "minimum_spend_minor": rule.minimum_spend_minor,
        "currency": rule.currency,
        "product_scope": rule.product_scope,
        "eligible_product_refs": rule.eligible_product_refs,
        "channel_scope": rule.channel_scope,
        "validity_mode": rule.validity_mode,
        "valid_days": rule.valid_days,
        "fixed_valid_from": rule.fixed_valid_from,
        "fixed_valid_until": rule.fixed_valid_until,
        "issuance_limit": rule.issuance_limit,
        "issued_count": rule.issued_count,
        "status": rule.status,
        "published_at": rule.published_at,
        "ended_at": rule.ended_at,
    }


async def _coupon_item(db: AsyncSession, tenant_id: uuid.UUID, coupon) -> CouponItem:
    rule = await get_coupon_rule(db, tenant_id, coupon.rule_version_id)
    return CouponItem(
        id=coupon.id,
        coupon_number=coupon.coupon_number,
        membership_id=coupon.membership_id,
        rule_version_id=coupon.rule_version_id,
        name=rule.name,
        amount_minor=rule.amount_minor,
        minimum_spend_minor=rule.minimum_spend_minor,
        product_scope=rule.product_scope,
        eligible_product_refs=rule.eligible_product_refs,
        channel_scope=rule.channel_scope,
        status=coupon.status,
        valid_from=coupon.valid_from,
        valid_until=coupon.valid_until,
        reserved_order_ref=coupon.reserved_order_ref,
        reservation_expires_at=coupon.reservation_expires_at,
        used_order_ref=coupon.used_order_ref,
        used_store_id=coupon.used_store_id,
    )


@repurchase_coupon_router.post("/repurchase-coupon-rules", status_code=201, dependencies=_ADMIN_WRITE)
async def create_rule_endpoint(
    body: CouponRuleCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    rule = await create_coupon_rule(
        db,
        tenant_id=tenant_id,
        created_by=account_id,
        **body.model_dump(),
    )
    return _rule_dict(rule)


@repurchase_coupon_router.post("/repurchase-coupon-rules/{rule_version_id}/transition", dependencies=_ADMIN_WRITE)
async def transition_rule_endpoint(
    rule_version_id: uuid.UUID,
    body: CouponRuleTransition,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    rule = await transition_coupon_rule(
        db,
        tenant_id=tenant_id,
        rule_version_id=rule_version_id,
        action=body.action,
        idempotency_key=body.idempotency_key,
        actor_id=account_id,
    )
    return _rule_dict(rule)


@repurchase_coupon_router.post("/repurchase-coupons/issue", status_code=201, dependencies=_ADMIN_WRITE)
async def issue_coupon_endpoint(
    body: CouponIssueRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        actor_type="brand",
        actor_id=account_id,
        **body.model_dump(),
    )
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.post("/repurchase-coupons/{coupon_id}/reserve", dependencies=_ADMIN_WRITE)
async def reserve_coupon_endpoint(
    coupon_id: uuid.UUID,
    body: CouponReserveRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    coupon = await reserve_member_coupon(
        db, tenant_id=tenant_id, coupon_id=coupon_id, actor_id=account_id, **body.model_dump()
    )
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.post("/repurchase-coupons/{coupon_id}/commit", dependencies=_ADMIN_WRITE)
async def commit_coupon_endpoint(
    coupon_id: uuid.UUID,
    body: CouponOrderTransition,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    coupon = await commit_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon_id,
        actor_type="brand",
        actor_id=account_id,
        **body.model_dump(),
    )
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.post("/repurchase-coupons/{coupon_id}/release", dependencies=_ADMIN_WRITE)
async def release_coupon_endpoint(
    coupon_id: uuid.UUID,
    body: CouponOrderTransition,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    coupon = await release_member_coupon(
        db, tenant_id=tenant_id, coupon_id=coupon_id, actor_type="brand", **body.model_dump()
    )
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.post("/repurchase-coupons/{coupon_id}/reverse", dependencies=_ADMIN_WRITE)
async def reverse_coupon_endpoint(
    coupon_id: uuid.UUID,
    body: CouponReverseRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    coupon = await reverse_member_coupon(db, tenant_id=tenant_id, coupon_id=coupon_id, **body.model_dump())
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.post("/repurchase-coupons/{coupon_id}/revoke", dependencies=_ADMIN_WRITE)
async def revoke_coupon_endpoint(
    coupon_id: uuid.UUID,
    body: CouponRevokeRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    coupon = await revoke_member_coupon(
        db, tenant_id=tenant_id, coupon_id=coupon_id, actor_id=account_id, **body.model_dump()
    )
    return await _coupon_item(db, tenant_id, coupon)


@repurchase_coupon_router.get("/consumers/membership/coupons", response_model=list[CouponItem])
async def consumer_wallet_endpoint(
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="active_membership_required")
    coupons = await list_member_wallet(db, tenant_id=tenant_id, membership_id=membership["membership_id"])
    return [await _coupon_item(db, tenant_id, coupon) for coupon in coupons]


@repurchase_coupon_router.post(
    "/consumers/membership/coupons/{coupon_id}/store-token",
    response_model=StoreRedemptionTokenResponse,
)
async def store_token_endpoint(
    coupon_id: uuid.UUID,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="active_membership_required")
    coupon = await get_member_coupon(db, tenant_id, coupon_id)
    if (
        coupon.membership_id != membership["membership_id"]
        or coupon.status != "available"
        or coupon.valid_from > utcnow()
        or coupon.valid_until <= utcnow()
        or (coupon.authority_type == "external" and coupon.sync_status != "synchronized")
    ):
        raise HTTPException(status_code=409, detail="coupon_not_available")
    rule = await get_coupon_rule(db, tenant_id, coupon.rule_version_id)
    if rule.channel_scope not in {"store", "both"}:
        raise HTTPException(status_code=409, detail="coupon_channel_not_allowed")
    return StoreRedemptionTokenResponse(
        redemption_token=issue_store_redemption_token(tenant_id, coupon.membership_id, coupon.id)
    )


@repurchase_coupon_router.post(
    "/repurchase-coupons/store/redeem",
    dependencies=[Depends(require_store_portal_principal)],
)
async def store_redeem_endpoint(
    body: StoreRedemptionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    membership_id, coupon_id, token_jti = decode_store_redemption_token(body.redemption_token, tenant_id)
    scope = await get_account_scope(db, tenant_id, account_id, "store")
    if scope is None or scope.store_id is None:
        raise HTTPException(status_code=403, detail="store_scope_required")
    coupon = await redeem_member_coupon_at_store(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon_id,
        membership_id=membership_id,
        store_id=scope.store_id,
        idempotency_key=f"store-redemption:{token_jti}",
        actor_id=account_id,
    )
    return await _coupon_item(db, tenant_id, coupon)
