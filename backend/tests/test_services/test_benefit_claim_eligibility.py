import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.benefit_claim_eligibility import ClaimEligibilityError, validate_claim_eligibility


def _benefit(campaign_id: uuid.UUID | None = None, config: dict | None = None):
    return SimpleNamespace(campaign_id=campaign_id, config_json=config or {})


def _campaign(campaign_id: uuid.UUID, product_id: uuid.UUID, *, status: str = "active", rules: dict | None = None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=campaign_id,
        product_id=product_id,
        status=status,
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        rules_json=rules or {"participation_condition_type": "any_scan"},
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["draft", "paused", "ended"])
async def test_attached_benefit_requires_live_campaign_and_matching_product(status):
    db = AsyncMock()
    campaign_id = uuid.uuid4()
    scanned_product_id = uuid.uuid4()
    db.scalar.side_effect = [SimpleNamespace(is_first_scan=True), _campaign(campaign_id, uuid.uuid4(), status=status)]

    with pytest.raises(ClaimEligibilityError, match="campaign_not_eligible"):
        await validate_claim_eligibility(
            db,
            tenant_id=uuid.uuid4(),
            benefit=_benefit(campaign_id),
            scanned_product_id=scanned_product_id,
            public_id="CODE001",
            scan_event_id=uuid.uuid4(),
            consumer_id="anon:v1:" + "a" * 64,
        )


@pytest.mark.anyio
async def test_first_scan_rule_uses_bound_scan_event_fact():
    db = AsyncMock()
    campaign_id = uuid.uuid4()
    product_id = uuid.uuid4()
    db.scalar.side_effect = [
        SimpleNamespace(is_first_scan=False),
        _campaign(campaign_id, product_id, rules={"participation_condition_type": "first_scan"}),
    ]

    with pytest.raises(ClaimEligibilityError, match="first_scan_required"):
        await validate_claim_eligibility(
            db,
            tenant_id=uuid.uuid4(),
            benefit=_benefit(campaign_id),
            scanned_product_id=product_id,
            public_id="CODE001",
            scan_event_id=uuid.uuid4(),
            consumer_id="anon:v1:" + "a" * 64,
        )


@pytest.mark.anyio
async def test_member_only_rejects_anonymous_consumer():
    db = AsyncMock()
    campaign_id = uuid.uuid4()
    product_id = uuid.uuid4()
    db.scalar.side_effect = [
        SimpleNamespace(is_first_scan=True),
        _campaign(campaign_id, product_id, rules={"participation_condition_type": "member_only"}),
    ]

    with pytest.raises(ClaimEligibilityError, match="member_required"):
        await validate_claim_eligibility(
            db,
            tenant_id=uuid.uuid4(),
            benefit=_benefit(campaign_id),
            scanned_product_id=product_id,
            public_id="CODE001",
            scan_event_id=uuid.uuid4(),
            consumer_id="anon:v1:" + "a" * 64,
        )


@pytest.mark.anyio
async def test_standalone_campaign_period_benefit_is_not_claimable():
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(is_first_scan=True)

    with pytest.raises(ClaimEligibilityError, match="campaign_required"):
        await validate_claim_eligibility(
            db,
            tenant_id=uuid.uuid4(),
            benefit=_benefit(config={"validity_type": "campaign_period"}),
            scanned_product_id=uuid.uuid4(),
            public_id="CODE001",
            scan_event_id=uuid.uuid4(),
            consumer_id="anon:v1:" + "a" * 64,
        )
