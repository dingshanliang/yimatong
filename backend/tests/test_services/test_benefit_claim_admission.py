import inspect
import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import Request

from app.core.config import settings
from app.services.benefit_claim_admission import build_claim_consumer_id, build_claim_idempotency_key
from app.services.scan_token import ScanLaunchAuthority, create_scan_token, verify_scan_token


def test_claim_request_is_strict_and_bounds_legacy_body_token():
    from app.schemas.benefit_claim import BenefitClaimRequest

    benefit_id = uuid.uuid4()
    assert BenefitClaimRequest(benefit_id=benefit_id, scan_token=" token ").scan_token == "token"
    for payload in (
        {"benefit_id": benefit_id, "scan_token": "x" * 4097},
        {"benefit_id": benefit_id, "unexpected": "value"},
    ):
        with pytest.raises(ValidationError):
            BenefitClaimRequest(**payload)


def test_claim_rate_limit_identity_is_keyed_and_does_not_expose_ip():
    from app.api.v1.benefit_claims import _rate_limit_identity

    client_ip = "198.51.100.42"
    digest = _rate_limit_identity(client_ip)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert client_ip not in digest


def test_claim_bearer_token_precedes_legacy_body_token():
    from app.api.v1.benefit_claims import _claim_scan_token
    from app.schemas.benefit_claim import BenefitClaimRequest

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/benefit-claims",
            "headers": [(b"authorization", b"Bearer trusted-header-token")],
        }
    )
    body = BenefitClaimRequest(benefit_id=uuid.uuid4(), scan_token="stale-body-token")

    assert _claim_scan_token(request, body) == "trusted-header-token"


def test_h5_claim_request_never_pays_or_uses_redis_as_claim_authority():
    from app.api.v1.benefit_claims import claim_benefit_h5

    source = inspect.getsource(claim_benefit_h5)
    assert "claim_red_packet" not in source
    assert "_claim_cache" not in source
    assert "claim_benefit(" in source


def test_authoritative_claim_replay_is_returned_as_success():
    from app.api.v1.benefit_claims import _is_successful_claim_outcome

    assert _is_successful_claim_outcome("replayed") is True
    assert _is_successful_claim_outcome("success") is True
    assert _is_successful_claim_outcome("risk_paused") is False


def test_claim_idempotency_key_is_token_unique_and_opaque():
    tenant_id = str(uuid.uuid4())
    benefit_id = uuid.uuid4()
    scan_event_id = str(uuid.uuid4())
    first_token = create_scan_token("CODE-001", "ip-hash", tenant_id, scan_event_id=scan_event_id)
    second_token = create_scan_token("CODE-001", "ip-hash", tenant_id, scan_event_id=scan_event_id)
    first_payload = verify_scan_token(first_token)
    second_payload = verify_scan_token(second_token)

    first_key = build_claim_idempotency_key(first_payload, benefit_id)
    second_key = build_claim_idempotency_key(second_payload, benefit_id)

    assert first_key != second_key
    assert re.fullmatch(r"claim:v1:[0-9a-f]{64}", first_key)
    assert first_token not in first_key
    assert "CODE-001" not in first_key
    assert tenant_id not in first_key


def test_claim_idempotency_key_rejects_missing_authority_claims():
    token = create_scan_token("CODE-001", "ip-hash", str(uuid.uuid4()), scan_event_id=str(uuid.uuid4()))
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    benefit_id = uuid.uuid4()

    for field in ("tenant_id", "public_id", "jti", "scan_event_id"):
        malformed = dict(payload)
        malformed.pop(field)
        try:
            build_claim_idempotency_key(malformed, benefit_id)
        except ValueError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"missing {field} must fail closed")


def test_anonymous_consumer_identity_is_stable_and_opaque():
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "public_id": "CODE-001",
        "visitor_id": "visitor-private-value",
    }

    identity = build_claim_consumer_id(payload)

    assert re.fullmatch(r"anon:v1:[0-9a-f]{64}", identity)
    assert payload["public_id"] not in identity
    assert payload["visitor_id"] not in identity


@pytest.mark.anyio
async def test_claim_rejects_a_benefit_outside_the_token_release_campaign_without_mutation(db, launch_facts):
    from app.models.campaign import Benefit, Campaign
    from app.models.scan import ScanEvent
    from app.services.campaign import claim_benefit
    from app.services.launch import confirm_launch_release, create_launch_release, launch_confirmed_release

    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-foreign-benefit")
    await launch_confirmed_release(db, release, account_id, "launch-foreign-benefit")
    other_campaign = Campaign(
        tenant_id=tenant_id,
        product_id=campaign.product_id,
        name="另一活动",
        campaign_type="scan",
        status="active",
        start_at=datetime.now(UTC) - timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=1),
        rules_json={},
    )
    db.add(other_campaign)
    await db.flush()
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=other_campaign.id,
        name="不属于上线版本的权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=10,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    event = ScanEvent(
        tenant_id=tenant_id,
        public_id="LAUNCHCODE001",
        scan_time=datetime.now(UTC) + timedelta(seconds=1),
        is_valid_visit=True,
        environment="browser",
    )
    db.add_all([benefit, event])
    await db.flush()

    result = await claim_benefit(
        db,
        tenant_id,
        benefit.id,
        "consumer-foreign",
        "claim:v2:foreign",
        public_id=event.public_id,
        scan_event_id=event.id,
        scanned_product_id=campaign.product_id,
        launch_authority=ScanLaunchAuthority(
            launch_release_id=release.id,
            campaign_id=campaign.id,
            code_batch_id=batch.id,
            content_digest=release.content_digest,
        ),
    )

    assert result == {"status": "benefit_not_in_launch_release"}
    assert benefit.stock_used == 0

    standalone = Benefit(
        tenant_id=tenant_id,
        campaign_id=None,
        name="未绑定活动的权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=10,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(standalone)
    await db.flush()
    standalone_result = await claim_benefit(
        db,
        tenant_id,
        standalone.id,
        "consumer-standalone",
        "claim:v2:standalone",
        public_id=event.public_id,
        scan_event_id=event.id,
        scanned_product_id=campaign.product_id,
        launch_authority=ScanLaunchAuthority(
            launch_release_id=release.id,
            campaign_id=campaign.id,
            code_batch_id=batch.id,
            content_digest=release.content_digest,
        ),
    )
    assert standalone_result == {"status": "benefit_not_in_launch_release"}
    assert standalone.stock_used == 0


@pytest.mark.anyio
async def test_claim_rejects_a_suspended_token_release_before_replay_or_stock_mutation(db, launch_facts):
    from app.models.campaign import Benefit
    from app.models.scan import ScanEvent
    from app.services.campaign import claim_benefit
    from app.services.launch import (
        confirm_launch_release,
        create_launch_release,
        launch_confirmed_release,
        suspend_launch_release,
    )

    tenant_id, account_id, version, campaign, batch = launch_facts
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="上线活动权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=10,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(benefit)
    await db.flush()
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-suspended-claim")
    await launch_confirmed_release(db, release, account_id, "launch-suspended-claim")
    event = ScanEvent(
        tenant_id=tenant_id,
        public_id="LAUNCHCODE001",
        scan_time=datetime.now(UTC) + timedelta(seconds=1),
        is_valid_visit=True,
        environment="browser",
    )
    db.add(event)
    await db.flush()
    authority = ScanLaunchAuthority(
        launch_release_id=release.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
        content_digest=release.content_digest,
    )
    await suspend_launch_release(db, release, account_id, "暂停领取")

    result = await claim_benefit(
        db,
        tenant_id,
        benefit.id,
        "consumer-stale",
        "claim:v2:stale",
        public_id=event.public_id,
        scan_event_id=event.id,
        scanned_product_id=campaign.product_id,
        launch_authority=authority,
    )

    assert result == {"status": "launch_release_not_current"}
    assert benefit.stock_used == 0
    assert await db.scalar(select(Benefit.stock_used).where(Benefit.id == benefit.id)) == 0


@pytest.mark.anyio
async def test_claim_recomputes_canonical_readiness_and_invalidates_config_drift_before_stock(db, launch_facts):
    from app.models.campaign import Benefit, BenefitClaim
    from app.models.scan import ScanEvent
    from app.services.campaign import claim_benefit
    from app.services.launch import confirm_launch_release, create_launch_release, launch_confirmed_release

    tenant_id, account_id, version, campaign, batch = launch_facts
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="上线活动权益",
        benefit_type="platform_coupon",
        config_json={"coupon_name": "原始券"},
        stock_total=10,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(benefit)
    await db.flush()
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-config-drift-service")
    await launch_confirmed_release(db, release, account_id, "launch-config-drift-service")
    event = await db.scalar(
        select(ScanEvent).where(ScanEvent.tenant_id == tenant_id, ScanEvent.public_id == "LAUNCHCODE001")
    )
    authority = ScanLaunchAuthority(
        launch_release_id=release.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
        content_digest=release.content_digest,
    )

    benefit.config_json = {"coupon_name": "漂移后的券"}
    await db.flush()
    result = await claim_benefit(
        db,
        tenant_id,
        benefit.id,
        "consumer-config-drift",
        "claim:v2:config-drift",
        public_id=event.public_id,
        scan_event_id=event.id,
        scanned_product_id=campaign.product_id,
        launch_authority=authority,
    )

    assert result == {"status": "launch_release_not_current"}
    assert release.status == "invalidated"
    assert benefit.stock_used == 0
    assert await db.scalar(select(BenefitClaim.id)) is None
