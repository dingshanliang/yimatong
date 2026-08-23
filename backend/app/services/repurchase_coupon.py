"""Authoritative fixed-amount repurchase coupon lifecycle."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import jwt
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import _session_uses_postgresql
from app.models.campaign import Benefit
from app.models.member import BrandMembership
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponEvent, RepurchaseCouponRuleVersion
from app.utils import utcnow

CouponActor = Literal["consumer", "store", "brand", "service", "system"]


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail=code)


async def _call_authority(
    db: AsyncSession,
    *,
    function_name: Literal["mutate_repurchase_coupon_rule_authority", "mutate_member_coupon_authority"],
    tenant_id: uuid.UUID,
    action: str,
    payload: dict[str, Any],
) -> uuid.UUID:
    try:
        value = await db.scalar(
            text(f"SELECT public.{function_name}(:tenant_id,:action,CAST(:payload AS jsonb))"),
            {
                "tenant_id": tenant_id,
                "action": action,
                "payload": json.dumps(payload, separators=(",", ":"), default=str),
            },
        )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        message = str(exc.orig).splitlines()[0]
        detail = message.removeprefix("<class 'asyncpg.exceptions.").split(": ", 1)[-1]
        raise HTTPException(status_code=403 if sqlstate == "42501" else 409, detail=detail) from exc
    if value is None:
        raise _conflict("coupon_authority_returned_no_asset")
    return uuid.UUID(str(value))


async def _record_coupon_notification(db: AsyncSession, *, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
    await db.scalar(
        text("SELECT public.record_coupon_member_notification_authority(:tenant_id,:event_id)"),
        {"tenant_id": tenant_id, "event_id": event_id},
    )


def issue_store_redemption_token(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    coupon_id: uuid.UUID,
    *,
    expires_in: int = 300,
) -> str:
    if expires_in <= 0 or expires_in > 300:
        raise ValueError("store redemption token lifetime must be within five minutes")
    return jwt.encode(
        {
            "type": "store_coupon_redemption",
            "tenant_id": str(tenant_id),
            "membership_id": str(membership_id),
            "coupon_id": str(coupon_id),
            "jti": str(uuid.uuid4()),
            "exp": int(time.time()) + expires_in,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def decode_store_redemption_token(token: str, tenant_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "store_coupon_redemption" or uuid.UUID(payload["tenant_id"]) != tenant_id:
            raise ValueError
        return uuid.UUID(payload["membership_id"]), uuid.UUID(payload["coupon_id"]), str(uuid.UUID(payload["jti"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid_store_redemption_token") from exc


async def _replay_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    idempotency_key: str,
    payload_digest: str,
    event_type: str,
) -> RepurchaseCouponEvent | None:
    event = await db.scalar(
        select(RepurchaseCouponEvent).where(
            RepurchaseCouponEvent.tenant_id == tenant_id,
            RepurchaseCouponEvent.idempotency_key == idempotency_key,
        )
    )
    if event is not None and (event.payload_digest != payload_digest or event.event_type != event_type):
        raise _conflict("coupon_idempotency_conflict")
    return event


def _event(
    *,
    tenant_id: uuid.UUID,
    rule: RepurchaseCouponRuleVersion,
    event_type: str,
    idempotency_key: str,
    payload_digest: str,
    actor_type: CouponActor,
    coupon: MemberCoupon | None = None,
    actor_id: uuid.UUID | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    order_ref: str | None = None,
    store_id: uuid.UUID | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> RepurchaseCouponEvent:
    return RepurchaseCouponEvent(
        id=uuid7(),
        tenant_id=tenant_id,
        rule_version_id=rule.id,
        coupon_id=coupon.id if coupon else None,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        order_ref=order_ref,
        store_id=store_id,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        details=details or {},
    )


async def get_coupon_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_version_id: uuid.UUID, *, for_update: bool = False
) -> RepurchaseCouponRuleVersion:
    statement = select(RepurchaseCouponRuleVersion).where(
        RepurchaseCouponRuleVersion.tenant_id == tenant_id,
        RepurchaseCouponRuleVersion.id == rule_version_id,
    )
    if _session_uses_postgresql(db):
        statement = statement.execution_options(populate_existing=True)
    if for_update:
        statement = statement.with_for_update()
    rule = await db.scalar(statement)
    if rule is None:
        raise HTTPException(status_code=404, detail="coupon_rule_not_found")
    return rule


async def create_coupon_rule(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    rule_key: str,
    version: int,
    name: str,
    amount_minor: int,
    minimum_spend_minor: int,
    product_scope: str,
    eligible_product_refs: list[str],
    channel_scope: str,
    validity_mode: str,
    valid_days: int | None,
    fixed_valid_from: datetime | None,
    fixed_valid_until: datetime | None,
    issuance_limit: int,
    idempotency_key: str,
    created_by: uuid.UUID | None = None,
    benefit_id: uuid.UUID | None = None,
) -> RepurchaseCouponRuleVersion:
    payload = {
        "rule_key": rule_key,
        "version": version,
        "name": name,
        "amount_minor": amount_minor,
        "minimum_spend_minor": minimum_spend_minor,
        "product_scope": product_scope,
        "eligible_product_refs": sorted(eligible_product_refs),
        "channel_scope": channel_scope,
        "validity_mode": validity_mode,
        "valid_days": valid_days,
        "fixed_valid_from": fixed_valid_from,
        "fixed_valid_until": fixed_valid_until,
        "issuance_limit": issuance_limit,
        "benefit_id": benefit_id,
    }
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        rule_id = uuid7()
        await _call_authority(
            db,
            function_name="mutate_repurchase_coupon_rule_authority",
            tenant_id=tenant_id,
            action="create",
            payload={
                **payload,
                "rule_version_id": rule_id,
                "actor_id": created_by,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        prior = await _replay_event(
            db,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            event_type="rule_created",
        )
        return await get_coupon_rule(db, tenant_id, prior.rule_version_id if prior else rule_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="rule_created",
    )
    if prior is not None:
        return await get_coupon_rule(db, tenant_id, prior.rule_version_id)
    if benefit_id is not None:
        benefit = await db.scalar(select(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.id == benefit_id))
        if benefit is None:
            raise HTTPException(status_code=404, detail="benefit_not_found")
        if benefit.benefit_type != "platform_coupon" or benefit.connector_id is not None:
            raise _conflict("coupon_benefit_authority_invalid")
    rule = RepurchaseCouponRuleVersion(
        id=uuid7(),
        tenant_id=tenant_id,
        rule_key=rule_key.strip(),
        version=version,
        benefit_id=benefit_id,
        name=name.strip(),
        amount_minor=amount_minor,
        minimum_spend_minor=minimum_spend_minor,
        currency="CNY",
        product_scope=product_scope,
        eligible_product_refs=sorted(set(eligible_product_refs)),
        channel_scope=channel_scope,
        validity_mode=validity_mode,
        valid_days=valid_days,
        fixed_valid_from=fixed_valid_from,
        fixed_valid_until=fixed_valid_until,
        issuance_limit=issuance_limit,
        issued_count=0,
        status="draft",
        created_by=created_by,
    )
    db.add(rule)
    await db.flush()
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            event_type="rule_created",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="brand",
            actor_id=created_by,
            to_status="draft",
        )
    )
    await db.flush()
    return rule


async def transition_coupon_rule(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    action: Literal["publish", "pause", "resume", "end"],
    idempotency_key: str,
    actor_id: uuid.UUID | None,
) -> RepurchaseCouponRuleVersion:
    event_type = {
        "publish": "rule_published",
        "pause": "rule_paused",
        "resume": "rule_resumed",
        "end": "rule_ended",
    }[action]
    payload_digest = _digest({"rule_version_id": rule_version_id, "action": action})
    if _session_uses_postgresql(db):
        rule_id = await _call_authority(
            db,
            function_name="mutate_repurchase_coupon_rule_authority",
            tenant_id=tenant_id,
            action=action,
            payload={
                "rule_version_id": rule_version_id,
                "actor_id": actor_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_coupon_rule(db, tenant_id, rule_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type=event_type,
    )
    if prior is not None:
        return await get_coupon_rule(db, tenant_id, rule_version_id)
    rule = await get_coupon_rule(db, tenant_id, rule_version_id, for_update=True)
    expected = {"publish": "draft", "pause": "published", "resume": "paused"}
    if action in expected and rule.status != expected[action]:
        raise _conflict("coupon_rule_transition_invalid")
    if action == "end" and rule.status not in {"published", "paused"}:
        raise _conflict("coupon_rule_transition_invalid")
    old_status = rule.status
    now = utcnow()
    rule.status = {"publish": "published", "pause": "paused", "resume": "published", "end": "ended"}[action]
    if action == "publish":
        rule.published_at = now
    if action == "end":
        rule.ended_at = now
    rule.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="brand",
            actor_id=actor_id,
            from_status=old_status,
            to_status=rule.status,
        )
    )
    await db.flush()
    return rule


async def get_member_coupon(
    db: AsyncSession, tenant_id: uuid.UUID, coupon_id: uuid.UUID, *, for_update: bool = False
) -> MemberCoupon:
    statement = select(MemberCoupon).where(MemberCoupon.tenant_id == tenant_id, MemberCoupon.id == coupon_id)
    if _session_uses_postgresql(db):
        statement = statement.execution_options(populate_existing=True)
    if for_update:
        statement = statement.with_for_update()
    coupon = await db.scalar(statement)
    if coupon is None:
        raise HTTPException(status_code=404, detail="member_coupon_not_found")
    return coupon


async def list_member_wallet(db: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> list[MemberCoupon]:
    statement = (
        select(MemberCoupon)
        .where(MemberCoupon.tenant_id == tenant_id, MemberCoupon.membership_id == membership_id)
        .order_by(MemberCoupon.issued_at.desc())
    )
    coupons = list((await db.scalars(statement)).all())
    now = utcnow()
    for coupon in coupons:
        if coupon.status in {"available", "reserved"} and coupon.valid_until <= now:
            await expire_member_coupon(
                db,
                tenant_id=tenant_id,
                coupon_id=coupon.id,
                idempotency_key=f"coupon-expire:{coupon.id}:{int(coupon.valid_until.timestamp())}",
            )
    if _session_uses_postgresql(db):
        statement = statement.execution_options(populate_existing=True)
    return list((await db.scalars(statement)).all())


async def issue_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    idempotency_key: str,
    source_claim_id: uuid.UUID | None = None,
    source_scan_event_id: uuid.UUID | None = None,
    source_scan_time: datetime | None = None,
    source_public_id: str | None = None,
    actor_type: CouponActor = "service",
    actor_id: uuid.UUID | None = None,
) -> MemberCoupon:
    payload = {
        "membership_id": membership_id,
        "rule_version_id": rule_version_id,
        "source_claim_id": source_claim_id,
        "source_scan_event_id": source_scan_event_id,
        "source_scan_time": source_scan_time,
        "source_public_id": source_public_id,
    }
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        coupon_id = uuid7()
        event_id = uuid7()
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="issue",
            payload={
                **payload,
                "coupon_id": coupon_id,
                "coupon_number": f"RCP-{coupon_id.hex[:16].upper()}",
                "actor_type": actor_type,
                "actor_id": actor_id,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        await _record_coupon_notification(db, tenant_id=tenant_id, event_id=event_id)
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="issued",
    )
    if prior is not None and prior.coupon_id is not None:
        return await get_member_coupon(db, tenant_id, prior.coupon_id)
    rule = await get_coupon_rule(db, tenant_id, rule_version_id, for_update=True)
    if rule.status != "published" or rule.issued_count >= rule.issuance_limit:
        raise _conflict("coupon_rule_not_issuable")
    membership = await db.scalar(
        select(BrandMembership).where(
            BrandMembership.tenant_id == tenant_id,
            BrandMembership.id == membership_id,
            BrandMembership.status == "active",
        )
    )
    if membership is None:
        raise _conflict("active_membership_required")
    active = await db.scalar(
        select(MemberCoupon).where(
            MemberCoupon.tenant_id == tenant_id,
            MemberCoupon.membership_id == membership_id,
            MemberCoupon.rule_version_id == rule_version_id,
            MemberCoupon.status.in_(("available", "reserved")),
        )
    )
    if active is not None:
        return active
    now = utcnow()
    valid_from = now if rule.validity_mode == "relative" else rule.fixed_valid_from
    valid_until = (
        now + timedelta(days=rule.valid_days or 0) if rule.validity_mode == "relative" else rule.fixed_valid_until
    )
    if valid_from is None or valid_until is None or valid_until <= now:
        raise _conflict("coupon_rule_validity_exhausted")
    coupon_id = uuid7()
    coupon = MemberCoupon(
        id=coupon_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        rule_version_id=rule_version_id,
        source_claim_id=source_claim_id,
        source_scan_event_id=source_scan_event_id,
        source_scan_time=source_scan_time,
        source_public_id=source_public_id,
        coupon_number=f"RCP-{coupon_id.hex[:16].upper()}",
        status="available",
        valid_from=valid_from,
        valid_until=valid_until,
        authority_type="yimatong",
        sync_status="not_required",
        version=1,
    )
    rule.issued_count += 1
    rule.updated_at = now
    db.add(coupon)
    await db.flush()
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="issued",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type=actor_type,
            actor_id=actor_id,
            to_status="available",
        )
    )
    await db.flush()
    return coupon


async def _coupon_and_rule_for_update(
    db: AsyncSession, tenant_id: uuid.UUID, coupon_id: uuid.UUID
) -> tuple[MemberCoupon, RepurchaseCouponRuleVersion]:
    coupon = await get_member_coupon(db, tenant_id, coupon_id, for_update=True)
    rule = await get_coupon_rule(db, tenant_id, coupon.rule_version_id)
    return coupon, rule


async def reserve_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    membership_id: uuid.UUID,
    order_ref: str,
    goods_subtotal_minor: int,
    eligible_subtotal_minor: int,
    idempotency_key: str,
    actor_id: uuid.UUID | None = None,
    request_digest: str | None = None,
) -> MemberCoupon:
    payload = {
        "coupon_id": coupon_id,
        "membership_id": membership_id,
        "order_ref": order_ref,
        "goods_subtotal_minor": goods_subtotal_minor,
        "eligible_subtotal_minor": eligible_subtotal_minor,
    }
    if request_digest is not None:
        payload["request_digest"] = request_digest
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="reserve",
            payload={
                **payload,
                "actor_type": "consumer",
                "actor_id": actor_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="reserved",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    now = utcnow()
    if coupon.membership_id != membership_id:
        raise HTTPException(status_code=403, detail="coupon_membership_mismatch")
    if rule.channel_scope not in {"online", "both"}:
        raise _conflict("coupon_channel_not_allowed")
    if coupon.authority_type == "external" and coupon.sync_status != "synchronized":
        raise _conflict("coupon_external_pending")
    if coupon.status == "reserved" and coupon.reserved_order_ref == order_ref:
        return coupon
    if coupon.status != "available" or coupon.valid_from > now or coupon.valid_until <= now:
        raise _conflict("coupon_not_available")
    if goods_subtotal_minor < rule.minimum_spend_minor or eligible_subtotal_minor <= 0:
        raise _conflict("coupon_order_not_eligible")
    discount_minor = min(rule.amount_minor, eligible_subtotal_minor, goods_subtotal_minor - 1)
    if discount_minor <= 0:
        raise _conflict("coupon_order_not_eligible")
    coupon.status = "reserved"
    coupon.reserved_order_ref = order_ref
    coupon.reservation_expires_at = now + timedelta(minutes=15)
    coupon.reserved_discount_minor = discount_minor
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="reserved",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="consumer",
            actor_id=actor_id,
            from_status="available",
            to_status="reserved",
            order_ref=order_ref,
            details={"discount_minor": discount_minor},
        )
    )
    await db.flush()
    return coupon


async def commit_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    order_ref: str,
    idempotency_key: str,
    actor_type: CouponActor = "service",
    actor_id: uuid.UUID | None = None,
    amount_minor: int | None = None,
) -> MemberCoupon:
    payload = {"coupon_id": coupon_id, "order_ref": order_ref}
    if amount_minor is not None:
        payload["amount_minor"] = amount_minor
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        event_id = uuid7()
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="commit",
            payload={
                "coupon_id": coupon_id,
                "order_ref": order_ref,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        await _record_coupon_notification(db, tenant_id=tenant_id, event_id=event_id)
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="committed",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    now = utcnow()
    if coupon.status != "reserved" or coupon.reserved_order_ref != order_ref:
        raise _conflict("coupon_reservation_mismatch")
    if coupon.reservation_expires_at is None or coupon.reservation_expires_at <= now:
        raise _conflict("coupon_reservation_expired")
    coupon.status = "used"
    coupon.used_order_ref = order_ref
    coupon.used_at = now
    coupon.reserved_order_ref = None
    coupon.reservation_expires_at = None
    coupon.reserved_discount_minor = None
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="committed",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type=actor_type,
            actor_id=actor_id,
            from_status="reserved",
            to_status="used",
            order_ref=order_ref,
        )
    )
    await db.flush()
    return coupon


async def release_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    order_ref: str,
    idempotency_key: str,
    actor_type: CouponActor = "service",
    amount_minor: int | None = None,
) -> MemberCoupon:
    payload = {"coupon_id": coupon_id, "order_ref": order_ref}
    if amount_minor is not None:
        payload["amount_minor"] = amount_minor
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="release",
            payload={
                "coupon_id": coupon_id,
                "order_ref": order_ref,
                "actor_type": actor_type,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="released",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    if coupon.status != "reserved" or coupon.reserved_order_ref != order_ref:
        raise _conflict("coupon_reservation_mismatch")
    now = utcnow()
    coupon.status = "available" if coupon.valid_until > now else "expired"
    coupon.reserved_order_ref = None
    coupon.reservation_expires_at = None
    coupon.reserved_discount_minor = None
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="released",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type=actor_type,
            from_status="reserved",
            to_status=coupon.status,
            order_ref=order_ref,
        )
    )
    await db.flush()
    return coupon


async def reverse_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    order_ref: str,
    full_refund: bool,
    idempotency_key: str,
    amount_minor: int | None = None,
) -> MemberCoupon:
    payload = {"coupon_id": coupon_id, "order_ref": order_ref, "full_refund": full_refund}
    if amount_minor is not None:
        payload["amount_minor"] = amount_minor
    payload_digest = _digest(payload)
    event_type = "reversed" if full_refund else "refund_recorded"
    if _session_uses_postgresql(db):
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="reverse" if full_refund else "record_partial_refund",
            payload={
                "coupon_id": coupon_id,
                "order_ref": order_ref,
                "actor_type": "service",
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type=event_type,
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    if coupon.status != "used" or coupon.used_order_ref != order_ref:
        raise _conflict("coupon_use_mismatch")
    if not full_refund:
        db.add(
            _event(
                tenant_id=tenant_id,
                rule=rule,
                coupon=coupon,
                event_type="refund_recorded",
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                actor_type="service",
                from_status="used",
                to_status="used",
                order_ref=order_ref,
                details={"outcome": "partial_refund_no_coupon_restore"},
            )
        )
        await db.flush()
        return coupon
    now = utcnow()
    coupon.status = "available" if coupon.valid_until > now else "expired"
    coupon.used_order_ref = None
    coupon.used_store_id = None
    coupon.used_at = None
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="reversed",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="service",
            from_status="used",
            to_status=coupon.status,
            order_ref=order_ref,
        )
    )
    await db.flush()
    return coupon


async def redeem_member_coupon_at_store(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    membership_id: uuid.UUID,
    store_id: uuid.UUID,
    idempotency_key: str,
    actor_id: uuid.UUID,
) -> MemberCoupon:
    payload_digest = _digest(
        {"coupon_id": coupon_id, "membership_id": membership_id, "store_id": store_id, "actor_id": actor_id}
    )
    if _session_uses_postgresql(db):
        event_id = uuid7()
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="store_redeem",
            payload={
                "coupon_id": coupon_id,
                "membership_id": membership_id,
                "store_id": store_id,
                "actor_type": "store",
                "actor_id": actor_id,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        await _record_coupon_notification(db, tenant_id=tenant_id, event_id=event_id)
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="store_redeemed",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    now = utcnow()
    if coupon.membership_id != membership_id:
        raise HTTPException(status_code=403, detail="coupon_membership_mismatch")
    if rule.channel_scope not in {"store", "both"}:
        raise _conflict("coupon_channel_not_allowed")
    if coupon.authority_type == "external" and coupon.sync_status != "synchronized":
        raise _conflict("coupon_external_pending")
    if coupon.status != "available" or coupon.valid_from > now or coupon.valid_until <= now:
        raise _conflict("coupon_not_available")
    coupon.status = "used"
    coupon.used_store_id = store_id
    coupon.used_at = now
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="store_redeemed",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="store",
            actor_id=actor_id,
            from_status="available",
            to_status="used",
            store_id=store_id,
        )
    )
    await db.flush()
    return coupon


async def expire_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    idempotency_key: str,
) -> MemberCoupon:
    payload_digest = _digest({"coupon_id": coupon_id})
    if _session_uses_postgresql(db):
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="expire",
            payload={
                "coupon_id": coupon_id,
                "actor_type": "system",
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="expired",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    now = utcnow()
    if coupon.status not in {"available", "reserved"} or coupon.valid_until > now:
        raise _conflict("coupon_not_expirable")
    old_status = coupon.status
    coupon.status = "expired"
    coupon.reserved_order_ref = None
    coupon.reservation_expires_at = None
    coupon.reserved_discount_minor = None
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="expired",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="system",
            from_status=old_status,
            to_status="expired",
        )
    )
    await db.flush()
    return coupon


async def revoke_member_coupon(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    coupon_id: uuid.UUID,
    reason: str,
    idempotency_key: str,
    actor_id: uuid.UUID,
) -> MemberCoupon:
    reason = reason.strip()
    if not reason:
        raise _conflict("coupon_revoke_reason_required")
    payload_digest = _digest({"coupon_id": coupon_id, "reason": reason})
    if _session_uses_postgresql(db):
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="revoke",
            payload={
                "coupon_id": coupon_id,
                "reason": reason,
                "actor_type": "brand",
                "actor_id": actor_id,
                "event_id": uuid7(),
                "idempotency_key": idempotency_key,
                "payload_digest": payload_digest,
            },
        )
        return await get_member_coupon(db, tenant_id, returned_id)
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        event_type="revoked",
    )
    if prior is not None:
        return await get_member_coupon(db, tenant_id, coupon_id)
    coupon, rule = await _coupon_and_rule_for_update(db, tenant_id, coupon_id)
    if coupon.status != "available":
        raise _conflict("coupon_not_revocable")
    now = utcnow()
    coupon.status = "revoked"
    coupon.revoked_at = now
    coupon.revoke_reason = reason
    coupon.version += 1
    coupon.updated_at = now
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="revoked",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            actor_type="brand",
            actor_id=actor_id,
            from_status="available",
            to_status="revoked",
            reason=reason,
        )
    )
    await db.flush()
    return coupon
