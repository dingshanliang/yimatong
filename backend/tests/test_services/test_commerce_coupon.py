import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.commerce_integration import CommerceMemberReference, CommerceServiceCredential
from app.models.member import BrandMembership
from app.models.tenant import Tenant
from app.services.commerce_coupon import list_eligible_commerce_coupons, transition_commerce_coupon
from app.services.commerce_integration import create_commerce_connection
from app.services.repurchase_coupon import create_coupon_rule, issue_member_coupon, transition_coupon_rule


async def _authority(db):
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="Commerce coupon", slug=f"commerce-coupon-{tenant_id.hex[:8]}"))
    membership = BrandMembership(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_number=f"MBR-{tenant_id.hex[:12].upper()}",
        status="active",
        join_consent_id=uuid.uuid4(),
    )
    db.add(membership)
    await db.flush()
    connection, _ = await create_commerce_connection(
        db,
        tenant_id=tenant_id,
        external_tenant_ref="commerce-tenant",
        external_shop_ref="commerce-shop",
        base_url="https://commerce.example.test/api",
        capabilities=["coupon_lifecycle"],
        idempotency_key="commerce-coupon-connect",
        actor_id=actor_id,
    )
    credential = await db.scalar(
        select(CommerceServiceCredential).where(
            CommerceServiceCredential.connection_id == connection.id,
            CommerceServiceCredential.direction == "commerce_to_yimatong",
        )
    )
    assert credential is not None
    member_ref = "cmr_coupon_member"
    db.add(
        CommerceMemberReference(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            connection_id=connection.id,
            membership_id=membership.id,
            member_ref=member_ref,
        )
    )
    rule = await create_coupon_rule(
        db,
        tenant_id=tenant_id,
        rule_key="COMMERCE-COUPON",
        version=1,
        name="复购立减 10 元",
        amount_minor=1000,
        minimum_spend_minor=5000,
        product_scope="all",
        eligible_product_refs=[],
        channel_scope="online",
        validity_mode="relative",
        valid_days=30,
        fixed_valid_from=None,
        fixed_valid_until=None,
        issuance_limit=10,
        idempotency_key="commerce-coupon-rule-create",
    )
    rule = await transition_coupon_rule(
        db,
        tenant_id=tenant_id,
        rule_version_id=rule.id,
        action="publish",
        idempotency_key="commerce-coupon-rule-publish",
        actor_id=actor_id,
    )
    coupon = await issue_member_coupon(
        db,
        tenant_id=tenant_id,
        membership_id=membership.id,
        rule_version_id=rule.id,
        idempotency_key="commerce-coupon-issue",
    )
    await db.flush()
    return credential, connection, member_ref, coupon


def _lines(amount_fen: int = 6000):
    return [
        {
            "product_ref": "medusa-product-1",
            "sku_ref": "medusa-sku-1",
            "quantity": 2,
            "amount_fen": amount_fen,
        }
    ]


@pytest.mark.anyio
async def test_commerce_coupon_eligibility_is_connection_scoped_and_authoritative(db):
    credential, connection, member_ref, coupon = await _authority(db)

    eligible = await list_eligible_commerce_coupons(
        db,
        credential=credential,
        connection=connection,
        member_ref=member_ref,
        goods_subtotal_fen=6000,
        line_items=_lines(),
    )

    assert eligible == [
        {
            "coupon_ref": str(coupon.id),
            "display_name": "复购立减 10 元",
            "amount_fen": 1000,
            "minimum_spend_fen": 5000,
            "valid_until": coupon.valid_until,
        }
    ]
    with pytest.raises(HTTPException) as wrong_member:
        await list_eligible_commerce_coupons(
            db,
            credential=credential,
            connection=connection,
            member_ref="cmr_other_member",
            goods_subtotal_fen=6000,
            line_items=_lines(),
        )
    assert wrong_member.value.status_code == 403
    assert wrong_member.value.detail == "commerce_member_reference_scope_mismatch"

    connection.capabilities = ["order_events"]
    with pytest.raises(HTTPException) as missing_capability:
        await list_eligible_commerce_coupons(
            db,
            credential=credential,
            connection=connection,
            member_ref=member_ref,
            goods_subtotal_fen=6000,
            line_items=_lines(),
        )
    assert missing_capability.value.status_code == 403
    assert missing_capability.value.detail == "commerce_coupon_capability_required"


@pytest.mark.anyio
async def test_commerce_coupon_transition_preserves_partial_refund_and_restores_full_refund(db):
    credential, connection, member_ref, coupon = await _authority(db)
    common = {
        "db": db,
        "credential": credential,
        "connection": connection,
        "member_ref": member_ref,
        "coupon_ref": coupon.id,
        "order_id": "order_001",
    }

    reserved = await transition_commerce_coupon(
        **common,
        action="reserve",
        amount_fen=1000,
        idempotency_key="commerce-reserve-001",
        goods_subtotal_fen=6000,
        line_items=_lines(),
        full_refund=None,
    )
    assert reserved.status == "reserved"
    assert reserved.reserved_discount_minor == 1000
    replayed = await transition_commerce_coupon(
        **common,
        action="reserve",
        amount_fen=1000,
        idempotency_key="commerce-reserve-001",
        goods_subtotal_fen=6000,
        line_items=_lines(),
        full_refund=None,
    )
    assert replayed.status == "reserved"
    with pytest.raises(HTTPException) as changed_cart:
        await transition_commerce_coupon(
            **common,
            action="reserve",
            amount_fen=1000,
            idempotency_key="commerce-reserve-001",
            goods_subtotal_fen=6000,
            line_items=[{**_lines()[0], "product_ref": "medusa-product-2"}],
            full_refund=None,
        )
    assert changed_cart.value.status_code == 409
    assert changed_cart.value.detail == "coupon_idempotency_conflict"

    used = await transition_commerce_coupon(
        **common,
        action="commit",
        amount_fen=1000,
        idempotency_key="commerce-commit-001",
        goods_subtotal_fen=None,
        line_items=[],
        full_refund=None,
    )
    assert used.status == "used"

    partial = await transition_commerce_coupon(
        **common,
        action="reverse",
        amount_fen=500,
        idempotency_key="commerce-reverse-partial-001",
        goods_subtotal_fen=None,
        line_items=[],
        full_refund=False,
    )
    assert partial.status == "used"

    restored = await transition_commerce_coupon(
        **common,
        action="reverse",
        amount_fen=500,
        idempotency_key="commerce-reverse-full-001",
        goods_subtotal_fen=None,
        line_items=[],
        full_refund=True,
    )
    assert restored.status == "available"


@pytest.mark.anyio
async def test_commerce_coupon_reserve_rejects_client_discount_mismatch(db):
    credential, connection, member_ref, coupon = await _authority(db)
    with pytest.raises(HTTPException) as mismatch:
        await transition_commerce_coupon(
            db,
            credential=credential,
            connection=connection,
            member_ref=member_ref,
            coupon_ref=coupon.id,
            order_id="order_mismatch",
            action="reserve",
            amount_fen=999,
            idempotency_key="commerce-reserve-mismatch",
            goods_subtotal_fen=6000,
            line_items=_lines(),
            full_refund=None,
        )
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail == "commerce_coupon_amount_mismatch"
