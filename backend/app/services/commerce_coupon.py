"""Signed commerce adapter for authoritative repurchase coupons."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_session_tenant_context
from app.models.commerce_integration import (
    CommerceConnection,
    CommerceMemberReference,
    CommerceServiceCredential,
)
from app.models.commerce_order import CommerceProductMapping
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponRuleVersion
from app.services.repurchase_coupon import (
    commit_member_coupon,
    get_coupon_rule,
    list_member_wallet,
    release_member_coupon,
    reserve_member_coupon,
    reverse_member_coupon,
)
from app.utils import utcnow

CouponAction = Literal["reserve", "commit", "release", "reverse"]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scope_error() -> HTTPException:
    return HTTPException(status_code=403, detail="commerce_member_reference_scope_mismatch")


def _cart_request_digest(goods_subtotal_fen: int, line_items: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"goods_subtotal_fen": goods_subtotal_fen, "line_items": line_items},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _resolve_member_reference(
    db: AsyncSession,
    *,
    credential: CommerceServiceCredential,
    connection: CommerceConnection,
    member_ref: str,
) -> CommerceMemberReference:
    if (
        credential.tenant_id != connection.tenant_id
        or credential.connection_id != connection.id
        or credential.direction != "commerce_to_yimatong"
        or connection.status != "active"
    ):
        raise HTTPException(status_code=401, detail="invalid_commerce_credential")
    if "coupon_lifecycle" not in set(connection.capabilities or []):
        raise HTTPException(status_code=403, detail="commerce_coupon_capability_required")
    await set_session_tenant_context(db, credential.tenant_id)
    reference = await db.scalar(
        select(CommerceMemberReference).where(
            CommerceMemberReference.tenant_id == credential.tenant_id,
            CommerceMemberReference.connection_id == connection.id,
            CommerceMemberReference.member_ref == member_ref,
        )
    )
    if reference is None:
        raise _scope_error()
    return reference


async def _eligible_subtotal(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    rule: RepurchaseCouponRuleVersion,
    goods_subtotal_fen: int,
    line_items: list[dict[str, Any]],
) -> int:
    if rule.product_scope == "all":
        return goods_subtotal_fen
    product_refs = {str(item["product_ref"]) for item in line_items}
    mappings = (
        await db.scalars(
            select(CommerceProductMapping).where(
                CommerceProductMapping.tenant_id == tenant_id,
                CommerceProductMapping.connection_id == connection_id,
                CommerceProductMapping.external_product_ref.in_(product_refs),
            )
        )
    ).all()
    internal_by_external = {item.external_product_ref: str(item.product_id) for item in mappings}
    allowed = set(rule.eligible_product_refs)
    return sum(
        int(item["amount_fen"])
        for item in line_items
        if internal_by_external.get(str(item["product_ref"])) in allowed
    )


async def _coupon_offer(
    db: AsyncSession,
    *,
    coupon: MemberCoupon,
    connection: CommerceConnection,
    goods_subtotal_fen: int,
    line_items: list[dict[str, Any]],
) -> tuple[RepurchaseCouponRuleVersion, int] | None:
    rule = await get_coupon_rule(db, coupon.tenant_id, coupon.rule_version_id)
    now = utcnow()
    if (
        coupon.status != "available"
        or _aware(coupon.valid_from) > now
        or _aware(coupon.valid_until) <= now
        or rule.channel_scope not in {"online", "both"}
        or goods_subtotal_fen < rule.minimum_spend_minor
    ):
        return None
    eligible_subtotal = await _eligible_subtotal(
        db,
        tenant_id=coupon.tenant_id,
        connection_id=connection.id,
        rule=rule,
        goods_subtotal_fen=goods_subtotal_fen,
        line_items=line_items,
    )
    discount = min(rule.amount_minor, eligible_subtotal, goods_subtotal_fen - 1)
    return (rule, discount) if discount > 0 else None


async def list_eligible_commerce_coupons(
    db: AsyncSession,
    *,
    credential: CommerceServiceCredential,
    connection: CommerceConnection,
    member_ref: str,
    goods_subtotal_fen: int,
    line_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reference = await _resolve_member_reference(
        db, credential=credential, connection=connection, member_ref=member_ref
    )
    wallet = await list_member_wallet(
        db, tenant_id=credential.tenant_id, membership_id=reference.membership_id
    )
    result: list[dict[str, Any]] = []
    for coupon in wallet:
        offer = await _coupon_offer(
            db,
            coupon=coupon,
            connection=connection,
            goods_subtotal_fen=goods_subtotal_fen,
            line_items=line_items,
        )
        if offer is None:
            continue
        rule, discount = offer
        result.append(
            {
                "coupon_ref": str(coupon.id),
                "display_name": rule.name[:80],
                "amount_fen": discount,
                "minimum_spend_fen": rule.minimum_spend_minor,
                "valid_until": coupon.valid_until,
            }
        )
    return result


async def transition_commerce_coupon(
    db: AsyncSession,
    *,
    credential: CommerceServiceCredential,
    connection: CommerceConnection,
    member_ref: str,
    coupon_ref: uuid.UUID,
    order_id: str,
    amount_fen: int,
    action: CouponAction,
    idempotency_key: str,
    goods_subtotal_fen: int | None,
    line_items: list[dict[str, Any]],
    full_refund: bool | None,
) -> MemberCoupon:
    reference = await _resolve_member_reference(
        db, credential=credential, connection=connection, member_ref=member_ref
    )
    coupon = await db.scalar(
        select(MemberCoupon).where(
            MemberCoupon.tenant_id == credential.tenant_id,
            MemberCoupon.id == coupon_ref,
            MemberCoupon.membership_id == reference.membership_id,
        )
    )
    if coupon is None:
        raise _scope_error()
    if action == "reserve":
        if goods_subtotal_fen is None:
            raise HTTPException(status_code=422, detail="commerce_coupon_cart_required")
        rule = await get_coupon_rule(db, coupon.tenant_id, coupon.rule_version_id)
        eligible_subtotal = await _eligible_subtotal(
            db,
            tenant_id=credential.tenant_id,
            connection_id=connection.id,
            rule=rule,
            goods_subtotal_fen=goods_subtotal_fen,
            line_items=line_items,
        )
        expected_amount = min(rule.amount_minor, eligible_subtotal, goods_subtotal_fen - 1)
        if (
            rule.channel_scope not in {"online", "both"}
            or goods_subtotal_fen < rule.minimum_spend_minor
            or expected_amount <= 0
        ):
            raise HTTPException(status_code=409, detail="coupon_order_not_eligible")
        if amount_fen != expected_amount:
            raise HTTPException(status_code=409, detail="commerce_coupon_amount_mismatch")
        return await reserve_member_coupon(
            db,
            tenant_id=credential.tenant_id,
            coupon_id=coupon_ref,
            membership_id=reference.membership_id,
            order_ref=order_id,
            goods_subtotal_minor=goods_subtotal_fen,
            eligible_subtotal_minor=eligible_subtotal,
            idempotency_key=idempotency_key,
            request_digest=_cart_request_digest(goods_subtotal_fen, line_items),
        )
    if action == "commit":
        return await commit_member_coupon(
            db,
            tenant_id=credential.tenant_id,
            coupon_id=coupon_ref,
            order_ref=order_id,
            idempotency_key=idempotency_key,
            amount_minor=amount_fen,
        )
    if action == "release":
        return await release_member_coupon(
            db,
            tenant_id=credential.tenant_id,
            coupon_id=coupon_ref,
            order_ref=order_id,
            idempotency_key=idempotency_key,
            amount_minor=amount_fen,
        )
    if full_refund is None:
        raise HTTPException(status_code=422, detail="commerce_coupon_refund_scope_required")
    return await reverse_member_coupon(
        db,
        tenant_id=credential.tenant_id,
        coupon_id=coupon_ref,
        order_ref=order_id,
        full_refund=full_refund,
        idempotency_key=idempotency_key,
        amount_minor=amount_fen,
    )
