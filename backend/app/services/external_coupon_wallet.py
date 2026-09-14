"""外部权威券钱包服务（authority_type='external' 的 MemberCoupon 写入路径）。

补齐外部券钱包的状态机 writer：
- 领取时 issue（sync_status='pending'，事件 external_sync_pending）
- worker 发放成功 confirm（→ synchronized，事件 external_sync_confirmed）
- worker 终态失败 mark_error（→ error，事件 external_sync_error）
- 外部平台核销回流 consume（available → used，事件 external_sync_confirmed）

PostgreSQL 走 public.mutate_member_coupon_authority 的对应动作（yimatong_app 仅经
此函数写入）；SQLite 测试契约路径在本地镜像同等语义。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql
from app.models.connector import BenefitDelivery
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponRuleVersion
from app.services.repurchase_coupon import (
    _call_authority,
    _digest,
    _event,
    _record_coupon_notification,
    _replay_event,
)
from app.utils import utcnow


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail=code)


async def issue_external_member_coupon(
    db,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    claim_id: uuid.UUID,
    connector_id: uuid.UUID,
    source_scan_event_id: uuid.UUID | None = None,
    source_scan_time: datetime | None = None,
    source_public_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> MemberCoupon:
    """领取外部连接器券时创建 pending 状态的钱包资产；幂等键默认按 claim 派生。"""
    from app.models.member import BrandMembership

    key = idempotency_key or f"external-coupon-issue:{claim_id}"
    payload: dict[str, Any] = {
        "membership_id": membership_id,
        "rule_version_id": rule_version_id,
        "source_claim_id": claim_id,
        "source_scan_event_id": source_scan_event_id,
        "source_scan_time": source_scan_time,
        "source_public_id": source_public_id,
        "external_connector_id": connector_id,
        "external_coupon_ref": f"claim:{claim_id}",
    }
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        coupon_id = uuid7()
        event_id = uuid7()
        returned_id = await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="external_issue",
            payload={
                **payload,
                "coupon_id": coupon_id,
                "coupon_number": f"RCP-{coupon_id.hex[:16].upper()}",
                "actor_type": "consumer",
                "actor_id": actor_id,
                "event_id": event_id,
                "idempotency_key": key,
                "payload_digest": payload_digest,
            },
        )
        await _record_coupon_notification(db, tenant_id=tenant_id, event_id=event_id)
        from app.services.repurchase_coupon import get_member_coupon

        return await get_member_coupon(db, tenant_id, returned_id)

    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=key,
        payload_digest=payload_digest,
        event_type="external_sync_pending",
    )
    if prior is not None and prior.coupon_id is not None:
        from app.services.repurchase_coupon import get_member_coupon

        return await get_member_coupon(db, tenant_id, prior.coupon_id)
    from app.services.repurchase_coupon import get_coupon_rule

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
    valid_until = now + _relative_days(rule) if rule.validity_mode == "relative" else rule.fixed_valid_until
    if valid_from is None or valid_until is None or valid_until <= now:
        raise _conflict("coupon_rule_validity_exhausted")
    coupon_id = uuid7()
    coupon = MemberCoupon(
        id=coupon_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        rule_version_id=rule_version_id,
        source_claim_id=claim_id,
        source_scan_event_id=source_scan_event_id,
        source_scan_time=source_scan_time,
        source_public_id=source_public_id,
        coupon_number=f"RCP-{coupon_id.hex[:16].upper()}",
        status="available",
        valid_from=valid_from,
        valid_until=valid_until,
        authority_type="external",
        external_connector_id=connector_id,
        external_coupon_ref=f"claim:{claim_id}",
        sync_status="pending",
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
            event_type="external_sync_pending",
            idempotency_key=key,
            payload_digest=payload_digest,
            actor_type="consumer",
            actor_id=actor_id,
            to_status="available",
        )
    )
    await db.flush()
    return coupon


async def confirm_external_coupon_sync(db, *, tenant_id: uuid.UUID, claim_id: uuid.UUID, external_id: str) -> bool:
    """外部发放成功后把 claim 对应的 pending/error 外部券确认 synchronized。

    无对应钱包资产时返回 False（不影响发放结算本身）。
    """
    coupon = await _external_coupon_by_claim(db, tenant_id, claim_id)
    if coupon is None:
        return False
    key = f"external-coupon-confirm:{claim_id}"
    payload = {"claim_id": claim_id, "external_id": external_id}
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="external_sync_confirm",
            payload={
                "coupon_id": coupon.id,
                "external_id": external_id,
                "actor_type": "service",
                "event_id": uuid7(),
                "idempotency_key": key,
                "payload_digest": payload_digest,
            },
        )
        return True
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=key,
        payload_digest=payload_digest,
        event_type="external_sync_confirmed",
    )
    if prior is not None:
        return True
    if coupon.sync_status == "synchronized":
        return True
    if coupon.sync_status not in ("pending", "error"):
        raise _conflict("coupon_external_sync_invalid")
    rule = await db.scalar(
        select(RepurchaseCouponRuleVersion).where(
            RepurchaseCouponRuleVersion.tenant_id == tenant_id,
            RepurchaseCouponRuleVersion.id == coupon.rule_version_id,
        )
    )
    now = utcnow()
    coupon.sync_status = "synchronized"
    coupon.sync_error = None
    coupon.version += 1
    coupon.updated_at = now
    await db.flush()
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="external_sync_confirmed",
            idempotency_key=key,
            payload_digest=payload_digest,
            actor_type="service",
            from_status=coupon.status,
            to_status=coupon.status,
            details={"external_id": external_id},
        )
    )
    await db.flush()
    return True


async def mark_external_coupon_sync_error_by_claim(
    db, *, tenant_id: uuid.UUID, claim_id: uuid.UUID, reason: str
) -> bool:
    """发放终态失败时把 pending 外部券标记 error；无资产或已确认时为安全 no-op。"""
    coupon = await _external_coupon_by_claim(db, tenant_id, claim_id)
    if coupon is None or coupon.sync_status != "pending":
        return False
    key = f"external-coupon-sync-error:{claim_id}"
    payload = {"claim_id": claim_id}
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="external_mark_error",
            payload={
                "coupon_id": coupon.id,
                "reason": reason,
                "actor_type": "service",
                "event_id": uuid7(),
                "idempotency_key": key,
                "payload_digest": payload_digest,
            },
        )
        return True
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=key,
        payload_digest=payload_digest,
        event_type="external_sync_error",
    )
    if prior is not None:
        return True
    rule = await db.scalar(
        select(RepurchaseCouponRuleVersion).where(
            RepurchaseCouponRuleVersion.tenant_id == tenant_id,
            RepurchaseCouponRuleVersion.id == coupon.rule_version_id,
        )
    )
    now = utcnow()
    coupon.sync_status = "error"
    coupon.sync_error = reason[:500]
    coupon.version += 1
    coupon.updated_at = now
    await db.flush()
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="external_sync_error",
            idempotency_key=key,
            payload_digest=payload_digest,
            actor_type="service",
            from_status=coupon.status,
            to_status=coupon.status,
            details={"reason": reason[:500]},
        )
    )
    await db.flush()
    return True


async def consume_external_coupon(
    db,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    external_ref: str,
    external_data: dict,
) -> bool:
    """外部核销回流：按发放记录外部 ID 定位 claim，再定位外部券转 used。

    找不到对应发放/钱包资产时返回 False（回调仍 200，避免外部平台重推）。
    """
    deliveries = list(
        (
            await db.execute(
                select(BenefitDelivery)
                .where(
                    BenefitDelivery.tenant_id == tenant_id,
                    BenefitDelivery.connector_id == connector_id,
                    BenefitDelivery.external_id == external_ref,
                    BenefitDelivery.claim_id.is_not(None),
                )
                .order_by(BenefitDelivery.id)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(deliveries) != 1:
        return False
    coupon = await _external_coupon_by_claim(db, tenant_id, deliveries[0].claim_id)
    if coupon is None:
        return False
    external_event = str(external_data.get("event") or "")[:200]
    key = f"external-coupon-consume:{coupon.id}"
    payload = {"coupon_id": coupon.id, "external_ref": external_ref}
    payload_digest = _digest(payload)
    if _session_uses_postgresql(db):
        await _call_authority(
            db,
            function_name="mutate_member_coupon_authority",
            tenant_id=tenant_id,
            action="external_consume",
            payload={
                "coupon_id": coupon.id,
                "external_event": external_event,
                "actor_type": "service",
                "event_id": uuid7(),
                "idempotency_key": key,
                "payload_digest": payload_digest,
            },
        )
        return True
    prior = await _replay_event(
        db,
        tenant_id=tenant_id,
        idempotency_key=key,
        payload_digest=payload_digest,
        event_type="external_sync_confirmed",
    )
    if prior is not None:
        return True
    if coupon.sync_status != "synchronized":
        raise _conflict("coupon_external_pending")
    if coupon.status == "used":
        return True
    if coupon.status != "available":
        raise _conflict("coupon_not_available")
    rule = await db.scalar(
        select(RepurchaseCouponRuleVersion).where(
            RepurchaseCouponRuleVersion.tenant_id == tenant_id,
            RepurchaseCouponRuleVersion.id == coupon.rule_version_id,
        )
    )
    now = utcnow()
    coupon.status = "used"
    coupon.used_at = now
    coupon.version += 1
    coupon.updated_at = now
    await db.flush()
    db.add(
        _event(
            tenant_id=tenant_id,
            rule=rule,
            coupon=coupon,
            event_type="external_sync_confirmed",
            idempotency_key=key,
            payload_digest=payload_digest,
            actor_type="service",
            from_status="available",
            to_status="used",
            details={"external_event": external_event},
        )
    )
    await db.flush()
    return True


async def _external_coupon_by_claim(db, tenant_id: uuid.UUID, claim_id) -> MemberCoupon | None:
    return await db.scalar(
        select(MemberCoupon).where(
            MemberCoupon.tenant_id == tenant_id,
            MemberCoupon.source_claim_id == claim_id,
            MemberCoupon.authority_type == "external",
        )
    )


def _relative_days(rule: RepurchaseCouponRuleVersion):
    return timedelta(days=rule.valid_days or 0)
