import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.member import BrandMembership
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponEvent
from app.models.tenant import Tenant
from app.services.repurchase_coupon import (
    commit_member_coupon,
    create_coupon_rule,
    issue_member_coupon,
    list_member_wallet,
    redeem_member_coupon_at_store,
    release_member_coupon,
    reserve_member_coupon,
    reverse_member_coupon,
    revoke_member_coupon,
    transition_coupon_rule,
)
from app.utils import utcnow


async def _membership(db, suffix: str = "coupon") -> tuple[uuid.UUID, BrandMembership]:
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"Brand {suffix}", slug=f"brand-{suffix}-{tenant_id.hex[:8]}"))
    membership = BrandMembership(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_number=f"MBR-{tenant_id.hex[:12].upper()}",
        status="active",
        join_consent_id=uuid.uuid4(),
    )
    db.add(membership)
    await db.flush()
    return tenant_id, membership


async def _published_rule(db, tenant_id: uuid.UUID, *, channel_scope: str = "both"):
    rule = await create_coupon_rule(
        db,
        tenant_id=tenant_id,
        rule_key=f"REPURCHASE-{uuid.uuid4().hex[:8]}",
        version=1,
        name="复购立减 10 元",
        amount_minor=1000,
        minimum_spend_minor=5000,
        product_scope="all",
        eligible_product_refs=[],
        channel_scope=channel_scope,
        validity_mode="relative",
        valid_days=30,
        fixed_valid_from=None,
        fixed_valid_until=None,
        issuance_limit=100,
        idempotency_key=f"rule-create-{uuid.uuid4()}",
    )
    return await transition_coupon_rule(
        db,
        tenant_id=tenant_id,
        rule_version_id=rule.id,
        action="publish",
        idempotency_key=f"rule-publish-{rule.id}",
        actor_id=None,
    )


@pytest.mark.anyio
async def test_issue_is_idempotent_and_does_not_duplicate_active_rule_asset(db):
    tenant_id, membership = await _membership(db, "issue")
    rule = await _published_rule(db, tenant_id)
    kwargs = {
        "tenant_id": tenant_id,
        "membership_id": membership.id,
        "rule_version_id": rule.id,
        "idempotency_key": "issue-repurchase-0001",
    }

    issued = await issue_member_coupon(db, **kwargs)
    replayed = await issue_member_coupon(db, **kwargs)
    duplicate_eligible_scan = await issue_member_coupon(db, **{**kwargs, "idempotency_key": "issue-repurchase-0002"})

    assert replayed.id == issued.id
    assert duplicate_eligible_scan.id == issued.id
    assert rule.issued_count == 1
    assert await db.scalar(select(func.count()).select_from(MemberCoupon)) == 1


@pytest.mark.anyio
async def test_online_reserve_commit_and_full_refund_restore_coupon(db):
    tenant_id, membership = await _membership(db, "online")
    rule = await _published_rule(db, tenant_id, channel_scope="online")
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        idempotency_key="issue-online",
    )
    reserved = await reserve_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        membership_id=membership.id,
        order_ref="ORDER-001",
        goods_subtotal_minor=5000,
        eligible_subtotal_minor=3000,
        idempotency_key="reserve-online",
    )
    assert reserved.status == "reserved"
    assert reserved.reserved_discount_minor == 1000

    with pytest.raises(HTTPException) as wrong_order:
        await reserve_member_coupon(
            db,
            tenant_id=tenant_id,
            coupon_id=coupon.id,
            membership_id=membership.id,
            order_ref="ORDER-OTHER",
            goods_subtotal_minor=5000,
            eligible_subtotal_minor=3000,
            idempotency_key="reserve-other",
        )
    assert wrong_order.value.detail == "coupon_not_available"

    used = await commit_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        order_ref="ORDER-001",
        idempotency_key="commit-online",
    )
    assert used.status == "used"
    assert used.used_order_ref == "ORDER-001"

    partial = await reverse_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        order_ref="ORDER-001",
        full_refund=False,
        idempotency_key="partial-refund-no-return",
    )
    assert partial.status == "used"
    partial_replay = await reverse_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        order_ref="ORDER-001",
        full_refund=False,
        idempotency_key="partial-refund-no-return",
    )
    assert partial_replay.status == "used"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(RepurchaseCouponEvent)
            .where(
                RepurchaseCouponEvent.tenant_id == tenant_id,
                RepurchaseCouponEvent.idempotency_key == "partial-refund-no-return",
            )
        )
        == 1
    )
    with pytest.raises(HTTPException) as reused_key:
        await reverse_member_coupon(
            db,
            tenant_id=tenant_id,
            coupon_id=coupon.id,
            order_ref="ORDER-001",
            full_refund=True,
            idempotency_key="partial-refund-no-return",
        )
    assert reused_key.value.detail == "coupon_idempotency_conflict"

    restored = await reverse_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        order_ref="ORDER-001",
        full_refund=True,
        idempotency_key="full-refund-return",
    )
    assert restored.status == "available"
    assert restored.used_order_ref is None


@pytest.mark.anyio
async def test_release_and_revoke_require_correct_state_and_reason(db):
    tenant_id, membership = await _membership(db, "release")
    rule = await _published_rule(db, tenant_id)
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        idempotency_key="issue-release",
    )
    await reserve_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        membership_id=membership.id,
        order_ref="ORDER-RELEASE",
        goods_subtotal_minor=8000,
        eligible_subtotal_minor=8000,
        idempotency_key="reserve-release",
    )
    released = await release_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        order_ref="ORDER-RELEASE",
        idempotency_key="release-cancel",
    )
    assert released.status == "available"

    with pytest.raises(HTTPException) as reason_required:
        await revoke_member_coupon(
            db,
            tenant_id=tenant_id,
            coupon_id=coupon.id,
            reason=" ",
            idempotency_key="revoke-empty",
            actor_id=uuid.uuid4(),
        )
    assert reason_required.value.detail == "coupon_revoke_reason_required"

    revoked = await revoke_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        reason="活动配置错误，未使用券作废",
        idempotency_key="revoke-valid",
        actor_id=uuid.uuid4(),
    )
    assert revoked.status == "revoked"


@pytest.mark.anyio
async def test_store_redemption_is_atomic_idempotent_and_creates_only_coupon_facts(db):
    tenant_id, membership = await _membership(db, "store")
    rule = await _published_rule(db, tenant_id, channel_scope="store")
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        idempotency_key="issue-store",
    )
    store_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    kwargs = {
        "tenant_id": tenant_id,
        "coupon_id": coupon.id,
        "membership_id": membership.id,
        "store_id": store_id,
        "idempotency_key": "store-redeem-jti-1",
        "actor_id": actor_id,
    }

    used = await redeem_member_coupon_at_store(db, **kwargs)
    replayed = await redeem_member_coupon_at_store(db, **kwargs)

    assert replayed.id == used.id
    assert used.status == "used"
    assert used.used_store_id == store_id
    assert used.used_order_ref is None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(RepurchaseCouponEvent)
            .where(
                RepurchaseCouponEvent.tenant_id == tenant_id,
                RepurchaseCouponEvent.event_type == "store_redeemed",
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_wallet_lazily_expires_stale_available_asset(db):
    tenant_id, membership = await _membership(db, "expiry")
    rule = await _published_rule(db, tenant_id)
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        idempotency_key="issue-expiring-wallet",
    )

    with patch("app.services.repurchase_coupon.utcnow", return_value=utcnow() + timedelta(days=31)):
        wallet = await list_member_wallet(db, tenant_id=tenant_id, membership_id=membership.id)

    assert wallet[0].id == coupon.id
    assert wallet[0].status == "expired"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(RepurchaseCouponEvent)
            .where(
                RepurchaseCouponEvent.tenant_id == tenant_id,
                RepurchaseCouponEvent.event_type == "expired",
            )
        )
        == 1
    )
