"""外部券钱包闭环（external_coupon_wallet）SQLite 契约路径测试。"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.campaign import Benefit, BenefitClaim
from app.models.connector import BenefitDelivery, Connector
from app.models.member import BrandMembership
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponEvent
from app.models.tenant import Tenant
from app.services.external_coupon_wallet import (
    confirm_external_coupon_sync,
    consume_external_coupon,
    issue_external_member_coupon,
    mark_external_coupon_sync_error_by_claim,
)
from app.services.repurchase_coupon import (
    create_coupon_rule,
    reserve_member_coupon,
    transition_coupon_rule,
)


async def _membership(db, suffix: str = "wallet") -> tuple[uuid.UUID, BrandMembership]:
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
        rule_key=f"WALLET-{uuid.uuid4().hex[:8]}",
        version=1,
        name="有赞满减券",
        amount_minor=500,
        minimum_spend_minor=2000,
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


async def _connector(db, tenant_id: uuid.UUID) -> Connector:
    connector = Connector(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="有赞测试连接器",
        connector_type="youzan",
        config={"client_id": "cid"},
        secrets_encrypted=None,
        enabled=True,
    )
    db.add(connector)
    await db.flush()
    return connector


async def _claim(db, tenant_id: uuid.UUID, consumer_id: str = "consumer-ext-1") -> BenefitClaim:
    benefit = Benefit(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="有赞券权益",
        benefit_type="platform_coupon",
        config_json={"coupon_id": "YZ-TPL", "rule_version_id": None},
    )
    db.add(benefit)
    await db.flush()
    claim = BenefitClaim(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        benefit_id=benefit.id,
        consumer_id=consumer_id,
        idempotency_key=f"claim-{uuid.uuid4().hex[:12]}",
    )
    db.add(claim)
    await db.flush()
    return claim


async def _delivery(
    db, tenant_id: uuid.UUID, connector: Connector, claim: BenefitClaim, external_id: str
) -> BenefitDelivery:
    delivery = BenefitDelivery(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        connector_id=connector.id,
        claim_id=claim.id,
        consumer_id="consumer-ext-1",
        benefit_type="platform_coupon",
        benefit_config={},
        external_id=external_id,
        status="success",
    )
    db.add(delivery)
    await db.flush()
    return delivery


@pytest.mark.anyio
async def test_external_issue_creates_pending_wallet_asset_and_is_idempotent(db):
    tenant_id, membership = await _membership(db)
    rule = await _published_rule(db, tenant_id)
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    coupon = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )
    assert coupon.authority_type == "external"
    assert coupon.sync_status == "pending"
    assert coupon.status == "available"
    assert coupon.external_connector_id == connector.id
    assert coupon.external_coupon_ref == f"claim:{claim.id}"
    assert coupon.source_claim_id == claim.id

    replayed = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )
    assert replayed.id == coupon.id
    assert rule.issued_count == 1
    events = (
        (await db.execute(select(RepurchaseCouponEvent).where(RepurchaseCouponEvent.coupon_id == coupon.id)))
        .scalars()
        .all()
    )
    assert [event.event_type for event in events] == ["external_sync_pending"]


@pytest.mark.anyio
async def test_confirm_and_mark_error_transitions(db):
    tenant_id, membership = await _membership(db, "confirm")
    rule = await _published_rule(db, tenant_id)
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    coupon = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )

    assert await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-1") is True
    await db.refresh(coupon)
    assert coupon.sync_status == "synchronized"

    # 已确认后重复确认为幂等 no-op
    assert await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-1") is True

    # 无对应钱包资产时 False
    assert await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=uuid.uuid4(), external_id="X") is False

    # error 标记只作用于 pending：确认过的不改
    assert (
        await mark_external_coupon_sync_error_by_claim(db, tenant_id=tenant_id, claim_id=claim.id, reason="boom")
        is False
    )


@pytest.mark.anyio
async def test_mark_error_on_pending_coupon(db):
    tenant_id, membership = await _membership(db, "error")
    rule = await _published_rule(db, tenant_id)
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    coupon = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )
    assert (
        await mark_external_coupon_sync_error_by_claim(db, tenant_id=tenant_id, claim_id=claim.id, reason="dead_letter")
        is True
    )
    await db.refresh(coupon)
    assert coupon.sync_status == "error"
    assert coupon.sync_error == "dead_letter"

    # error 后发放成功可恢复为 synchronized
    assert await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-2") is True
    await db.refresh(coupon)
    assert coupon.sync_status == "synchronized"


@pytest.mark.anyio
async def test_consume_flows_from_delivery_external_id_to_used(db):
    tenant_id, membership = await _membership(db, "consume")
    rule = await _published_rule(db, tenant_id)
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    coupon = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )

    # 未同步前核销被核销守卫拦截
    await _delivery(db, tenant_id, connector, claim, "YZ-GRANT-77")
    with pytest.raises(HTTPException) as guard:
        await consume_external_coupon(db, tenant_id, connector.id, "YZ-GRANT-77", {"event": "coupon_consume"})
    assert guard.value.status_code == 409

    await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-GRANT-77")
    assert (
        await consume_external_coupon(db, tenant_id, connector.id, "YZ-GRANT-77", {"event": "coupon_consume"}) is True
    )
    await db.refresh(coupon)
    assert coupon.status == "used"
    assert coupon.used_at is not None
    assert coupon.used_order_ref is None
    assert coupon.used_store_id is None

    # 重复核销幂等
    assert (
        await consume_external_coupon(db, tenant_id, connector.id, "YZ-GRANT-77", {"event": "coupon_consume"}) is True
    )

    # 外部引用查无发放记录 → False 不抛
    assert await consume_external_coupon(db, tenant_id, connector.id, "YZ-UNKNOWN", {}) is False


@pytest.mark.anyio
async def test_consume_is_tenant_scoped(db):
    tenant_a, membership_a = await _membership(db, "tenant-a")
    rule_a = await _published_rule(db, tenant_a)
    connector_a = await _connector(db, tenant_a)
    claim_a = await _claim(db, tenant_a)

    tenant_b, _membership_b = await _membership(db, "tenant-b")

    await issue_external_member_coupon(
        db,
        tenant_id=tenant_a,
        membership_id=membership_a.id,
        rule_version_id=rule_a.id,
        claim_id=claim_a.id,
        connector_id=connector_a.id,
    )
    await confirm_external_coupon_sync(db, tenant_id=tenant_a, claim_id=claim_a.id, external_id="YZ-X")
    await _delivery(db, tenant_a, connector_a, claim_a, "YZ-X")

    # B 租户上下文无法消费 A 租户的发放
    assert await consume_external_coupon(db, tenant_b, connector_a.id, "YZ-X", {}) is False
    assert (
        await db.scalar(select(func.count()).select_from(MemberCoupon).where(MemberCoupon.tenant_id == tenant_b)) == 0
    )


@pytest.mark.anyio
async def test_pending_external_coupon_blocked_from_redeem_paths(db):
    tenant_id, membership = await _membership(db, "guard")
    rule = await _published_rule(db, tenant_id, channel_scope="both")
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    coupon = await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )
    with pytest.raises(HTTPException) as guard:
        await reserve_member_coupon(
            db,
            tenant_id=tenant_id,
            coupon_id=coupon.id,
            membership_id=membership.id,
            order_ref="ORDER-EXT-1",
            goods_subtotal_minor=5000,
            eligible_subtotal_minor=3000,
            idempotency_key="reserve-ext-1",
        )
    assert guard.value.detail == "coupon_external_pending"

    # confirm 修改的是同一会话对象（identity map），无需 refresh——refresh 会把
    # SQLite 存回的 naive datetime 引入后续比较
    await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-G")
    assert coupon.sync_status == "synchronized"
    reserved = await reserve_member_coupon(
        db,
        tenant_id=tenant_id,
        coupon_id=coupon.id,
        membership_id=membership.id,
        order_ref="ORDER-EXT-1",
        goods_subtotal_minor=5000,
        eligible_subtotal_minor=3000,
        idempotency_key="reserve-ext-2",
    )
    assert reserved.status == "reserved"


@pytest.mark.anyio
async def test_consume_rejects_ambiguous_external_id_with_multiple_deliveries(db):
    """同一 external_id 命中多条发放记录时拒绝核销回流（防多义映射）。"""
    tenant_id, membership = await _membership(db, "ambiguous")
    rule = await _published_rule(db, tenant_id)
    connector = await _connector(db, tenant_id)
    claim = await _claim(db, tenant_id)

    await issue_external_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        claim_id=claim.id,
        connector_id=connector.id,
    )
    await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim.id, external_id="YZ-AMB-1")
    await _delivery(db, tenant_id, connector, claim, "YZ-AMB-1")
    await _delivery(db, tenant_id, connector, claim, "YZ-AMB-1")

    assert await consume_external_coupon(db, tenant_id, connector.id, "YZ-AMB-1", {}) is False
