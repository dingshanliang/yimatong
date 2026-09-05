import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.consent import (
    ConsentRecord,
    ConsumerConsentAction,
    ConsumerConsentPolicy,
    ConsumerConsentPolicyCurrent,
)
from app.models.member import BrandMembership, ConsumerProfile, MemberIdentityCredential
from app.models.repurchase_coupon import MemberCoupon, RepurchaseCouponRuleVersion
from app.models.tenant import Tenant
from app.services.brand_membership import (
    bind_verified_member_identity,
    get_brand_membership_for_profile,
    issue_member_recovery_token,
    join_brand_membership,
    merge_brand_memberships,
    recover_brand_membership,
)


async def _tenant(db, suffix: str = "member") -> uuid.UUID:
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"Brand {suffix}", slug=f"brand-{suffix}-{tenant_id.hex[:8]}"))
    await db.flush()
    return tenant_id


async def _consent(db, tenant_id: uuid.UUID, *, purpose: str = "brand_membership"):
    scan_event_id = uuid.uuid4()
    scan_time = datetime.now(UTC)
    current_policy = await db.scalar(
        select(ConsumerConsentPolicyCurrent).where(
            ConsumerConsentPolicyCurrent.tenant_id == tenant_id,
            ConsumerConsentPolicyCurrent.purpose == purpose,
        )
    )
    if current_policy is None:
        policy_id = uuid.uuid4()
        db.add(
            ConsumerConsentPolicy(
                id=policy_id,
                tenant_id=tenant_id,
                purpose=purpose,
                consent_type="privacy" if purpose == "brand_membership" else "marketing",
                policy_version="test-v1",
                policy_digest="d" * 64,
                policy_title="测试同意政策",
                policy_content="仅用于测试明确同意权威。",
                effective_at=scan_time,
            )
        )
        db.add(ConsumerConsentPolicyCurrent(tenant_id=tenant_id, purpose=purpose, policy_id=policy_id))
        await db.flush()
    else:
        policy_id = current_policy.policy_id
    visitor_subject_hash = uuid.uuid4().hex.ljust(64, "0")
    consent = ConsentRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        consent_type="privacy",
        status="granted",
        public_id="MEMBER-CODE",
        purpose=purpose,
        policy_id=policy_id,
        policy_version="test-v1",
        policy_digest="d" * 64,
        visitor_subject_hash=visitor_subject_hash,
        scan_event_id=scan_event_id,
        scan_event_time=scan_time,
        idempotency_key=f"consent-{uuid.uuid4()}",
        authority_version=1,
    )
    db.add(consent)
    await db.flush()
    db.add(
        ConsumerConsentAction(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            consent_id=consent.id,
            policy_id=policy_id,
            consumer_id=None,
            action="grant",
            idempotency_key=f"grant-{uuid.uuid4()}",
            payload_hash="a" * 64,
            visitor_subject_hash=visitor_subject_hash,
            result_status="granted",
        )
    )
    await db.flush()
    return consent, scan_event_id, scan_time


@pytest.mark.anyio
async def test_profile_or_contact_does_not_implicitly_create_membership(db):
    tenant_id = await _tenant(db, "profile-only")
    profile = ConsumerProfile(id=uuid.uuid4(), tenant_id=tenant_id, nickname="已留资消费者")
    db.add(profile)
    await db.flush()

    assert await get_brand_membership_for_profile(db, tenant_id, profile.id) is None


@pytest.mark.anyio
async def test_explicit_join_is_idempotent_and_uses_membership_consent(db):
    tenant_id = await _tenant(db, "join")
    consent, scan_event_id, scan_time = await _consent(db, tenant_id)
    kwargs = {
        "tenant_id": tenant_id,
        "consent_id": consent.id,
        "scan_event_id": scan_event_id,
        "scan_time": scan_time,
        "public_id": "MEMBER-CODE",
        "visitor_id": str(uuid.uuid4()),
        "token_consumer_id": None,
        "idempotency_key": "membership-join-0001",
    }

    created = await join_brand_membership(db, **kwargs)
    replayed = await join_brand_membership(db, **kwargs)

    assert created["membership_number"].startswith("MBR-")
    assert replayed["membership_id"] == created["membership_id"]
    assert replayed["consumer_id"] == created["consumer_id"]
    assert replayed["replayed"] is True
    assert consent.consumer_id == created["consumer_id"]


@pytest.mark.anyio
async def test_join_rejects_lead_or_marketing_consent(db):
    tenant_id = await _tenant(db, "wrong-consent")
    consent, scan_event_id, scan_time = await _consent(db, tenant_id, purpose="lead_capture")

    with pytest.raises(HTTPException) as exc:
        await join_brand_membership(
            db,
            tenant_id=tenant_id,
            consent_id=consent.id,
            scan_event_id=scan_event_id,
            scan_time=scan_time,
            public_id="MEMBER-CODE",
            visitor_id=str(uuid.uuid4()),
            token_consumer_id=None,
            idempotency_key="membership-join-0002",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "membership_consent_required"


@pytest.mark.anyio
async def test_join_rejects_legacy_consent_without_authority_receipt(db):
    tenant_id = await _tenant(db, "legacy-consent")
    consent, scan_event_id, scan_time = await _consent(db, tenant_id)
    consent.authority_version = 0
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await join_brand_membership(
            db,
            tenant_id=tenant_id,
            consent_id=consent.id,
            scan_event_id=scan_event_id,
            scan_time=scan_time,
            public_id="MEMBER-CODE",
            visitor_id=str(uuid.uuid4()),
            token_consumer_id=None,
            idempotency_key="membership-join-legacy-consent",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "membership_consent_required"


@pytest.mark.anyio
async def test_verified_identity_is_issuer_scoped_and_recovers_same_brand_member(db):
    tenant_id = await _tenant(db, "recover")
    consent, scan_event_id, scan_time = await _consent(db, tenant_id)
    joined = await join_brand_membership(
        db,
        tenant_id=tenant_id,
        consent_id=consent.id,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id="MEMBER-CODE",
        visitor_id=str(uuid.uuid4()),
        token_consumer_id=None,
        idempotency_key="membership-join-0003",
    )
    credential = await bind_verified_member_identity(
        db,
        tenant_id=tenant_id,
        membership_id=joined["membership_id"],
        credential_type="wechat_openid",
        issuer="wx-shared-appid",
        subject="openid-verified-by-wechat",
        verification_receipt_hash="1" * 64,
        idempotency_key="identity-bind-0001",
    )
    new_profile = ConsumerProfile(id=uuid.uuid4(), tenant_id=tenant_id)
    db.add(new_profile)
    await db.flush()
    token = issue_member_recovery_token(tenant_id, joined["membership_id"], credential.id)

    recovered = await recover_brand_membership(
        db,
        tenant_id=tenant_id,
        current_consumer_id=new_profile.id,
        recovery_token=token,
        idempotency_key="membership-recover-0001",
    )

    assert recovered["membership_id"] == joined["membership_id"]
    assert (await get_brand_membership_for_profile(db, tenant_id, new_profile.id))["membership_id"] == joined[
        "membership_id"
    ]

    other_profile = ConsumerProfile(id=uuid.uuid4(), tenant_id=tenant_id)
    db.add(other_profile)
    await db.flush()
    with pytest.raises(HTTPException) as reused:
        await recover_brand_membership(
            db,
            tenant_id=tenant_id,
            current_consumer_id=other_profile.id,
            recovery_token=token,
            idempotency_key="membership-recover-another-request",
        )
    assert reused.value.status_code == 409
    assert reused.value.detail == "member_recovery_token_already_used"


@pytest.mark.anyio
async def test_conflicting_memberships_require_two_distinct_proofs_and_merge_idempotently(db):
    tenant_id = await _tenant(db, "merge")
    source_consent, source_scan_id, source_scan_time = await _consent(db, tenant_id)
    source = await join_brand_membership(
        db,
        tenant_id=tenant_id,
        consent_id=source_consent.id,
        scan_event_id=source_scan_id,
        scan_time=source_scan_time,
        public_id="MEMBER-CODE",
        visitor_id=str(uuid.uuid4()),
        token_consumer_id=None,
        idempotency_key="membership-join-source",
    )
    target_consent, target_scan_id, target_scan_time = await _consent(db, tenant_id)
    target = await join_brand_membership(
        db,
        tenant_id=tenant_id,
        consent_id=target_consent.id,
        scan_event_id=target_scan_id,
        scan_time=target_scan_time,
        public_id="MEMBER-CODE",
        visitor_id=str(uuid.uuid4()),
        token_consumer_id=None,
        idempotency_key="membership-join-target",
    )
    source_credential = await bind_verified_member_identity(
        db,
        tenant_id=tenant_id,
        membership_id=source["membership_id"],
        credential_type="wechat_openid",
        issuer="wx-source-appid",
        subject="source-openid",
        verification_receipt_hash="2" * 64,
        idempotency_key="identity-bind-source",
    )
    target_credential = await bind_verified_member_identity(
        db,
        tenant_id=tenant_id,
        membership_id=target["membership_id"],
        credential_type="wechat_openid",
        issuer="wx-target-appid",
        subject="target-openid",
        verification_receipt_hash="3" * 64,
        idempotency_key="identity-bind-target",
    )
    source_token = issue_member_recovery_token(tenant_id, source["membership_id"], source_credential.id)
    target_token = issue_member_recovery_token(tenant_id, target["membership_id"], target_credential.id)

    now = datetime.now(UTC)
    rule = RepurchaseCouponRuleVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        rule_key="MERGE-ASSET",
        version=1,
        name="合并资产保全券",
        amount_minor=500,
        minimum_spend_minor=0,
        currency="CNY",
        product_scope="all",
        eligible_product_refs=[],
        channel_scope="both",
        validity_mode="relative",
        valid_days=30,
        issuance_limit=10,
        issued_count=2,
        status="published",
        published_at=now,
    )
    source_coupon = MemberCoupon(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=source["membership_id"],
        rule_version_id=rule.id,
        coupon_number=f"RCP-{uuid.uuid4().hex[:16].upper()}",
        status="reserved",
        valid_from=now,
        valid_until=now + timedelta(days=30),
        reserved_order_ref="ORDER-MERGING",
        reservation_expires_at=now + timedelta(minutes=15),
        reserved_discount_minor=500,
        authority_type="yimatong",
        sync_status="not_required",
        version=1,
    )
    target_coupon = MemberCoupon(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=target["membership_id"],
        rule_version_id=rule.id,
        coupon_number=f"RCP-{uuid.uuid4().hex[:16].upper()}",
        status="available",
        valid_from=now,
        valid_until=now + timedelta(days=30),
        authority_type="yimatong",
        sync_status="not_required",
        version=1,
    )
    db.add_all([rule, source_coupon, target_coupon])
    await db.flush()

    with pytest.raises(HTTPException) as one_proof:
        await recover_brand_membership(
            db,
            tenant_id=tenant_id,
            current_consumer_id=source["consumer_id"],
            recovery_token=target_token,
            idempotency_key="single-proof-recovery",
        )
    assert one_proof.value.detail == "membership_merge_requires_two_verified_credentials"

    with pytest.raises(HTTPException) as reserved_asset:
        await merge_brand_memberships(
            db,
            tenant_id=tenant_id,
            current_consumer_id=source["consumer_id"],
            current_recovery_token=source_token,
            target_recovery_token=target_token,
            idempotency_key="membership-merge-reserved",
        )
    assert reserved_asset.value.detail == "membership_merge_blocked_by_reserved_coupon"
    source_coupon.status = "available"
    source_coupon.reserved_order_ref = None
    source_coupon.reservation_expires_at = None
    source_coupon.reserved_discount_minor = None
    await db.flush()

    merged = await merge_brand_memberships(
        db,
        tenant_id=tenant_id,
        current_consumer_id=source["consumer_id"],
        current_recovery_token=source_token,
        target_recovery_token=target_token,
        idempotency_key="membership-merge-0001",
    )
    replayed = await merge_brand_memberships(
        db,
        tenant_id=tenant_id,
        current_consumer_id=source["consumer_id"],
        current_recovery_token=source_token,
        target_recovery_token=target_token,
        idempotency_key="membership-merge-0001",
    )

    assert merged["membership_id"] == target["membership_id"]
    assert replayed["replayed"] is True
    assert (await get_brand_membership_for_profile(db, tenant_id, source["consumer_id"]))["membership_id"] == target[
        "membership_id"
    ]
    source_record = await db.get(BrandMembership, source["membership_id"])
    assert source_record.status == "merged"
    assert source_record.merged_into_id == target["membership_id"]
    moved_credential = await db.get(MemberIdentityCredential, source_credential.id)
    assert moved_credential.membership_id == target["membership_id"]
    assert source_coupon.membership_id == target["membership_id"]
    assert target_coupon.membership_id == target["membership_id"]


@pytest.mark.anyio
async def test_recovery_token_cannot_cross_tenant(db):
    tenant_id = await _tenant(db, "source")
    other_tenant_id = await _tenant(db, "other")
    consent, scan_event_id, scan_time = await _consent(db, tenant_id)
    joined = await join_brand_membership(
        db,
        tenant_id=tenant_id,
        consent_id=consent.id,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id="MEMBER-CODE",
        visitor_id=str(uuid.uuid4()),
        token_consumer_id=None,
        idempotency_key="membership-join-0004",
    )
    credential = await bind_verified_member_identity(
        db,
        tenant_id=tenant_id,
        membership_id=joined["membership_id"],
        credential_type="wechat_openid",
        issuer="wx-shared-appid",
        subject="openid-cross-tenant",
        verification_receipt_hash="4" * 64,
        idempotency_key="identity-bind-0002",
    )
    other_profile = ConsumerProfile(id=uuid.uuid4(), tenant_id=other_tenant_id)
    db.add(other_profile)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await recover_brand_membership(
            db,
            tenant_id=other_tenant_id,
            current_consumer_id=other_profile.id,
            recovery_token=issue_member_recovery_token(tenant_id, joined["membership_id"], credential.id),
            idempotency_key="membership-recover-0002",
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid_member_recovery"
