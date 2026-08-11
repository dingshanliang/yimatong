"""Application-side fail-closed preflight for consumer benefit claims.

PostgreSQL repeats these checks inside ``claim_campaign_benefit`` while holding
the authoritative rows. This preflight prevents unusable OAuth/contact flows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, Campaign
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent


class ClaimEligibilityError(ValueError):
    pass


def _aware_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


async def validate_claim_eligibility(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    benefit: Benefit,
    scanned_product_id: uuid.UUID,
    public_id: str,
    scan_event_id: uuid.UUID,
    consumer_id: str,
) -> Campaign | None:
    event = await db.scalar(
        select(ScanEvent).where(
            ScanEvent.id == scan_event_id,
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id == public_id,
            ScanEvent.is_valid_visit.is_(True),
        )
    )
    if event is None:
        raise ClaimEligibilityError("scan_not_eligible")

    campaign = None
    if benefit.campaign_id is not None:
        campaign = await db.scalar(
            select(Campaign).where(Campaign.id == benefit.campaign_id, Campaign.tenant_id == tenant_id)
        )
        now = datetime.now(UTC)
        start_at = _aware_datetime(campaign.start_at) if campaign else None
        end_at = _aware_datetime(campaign.end_at) if campaign else None
        if (
            campaign is None
            or campaign.status != "active"
            or campaign.product_id != scanned_product_id
            or start_at is None
            or end_at is None
            or not (start_at <= now < end_at)
        ):
            raise ClaimEligibilityError("campaign_not_eligible")

        participation = (campaign.rules_json or {}).get("participation_condition_type", "any_scan")
        if participation == "first_scan" and not event.is_first_scan:
            raise ClaimEligibilityError("first_scan_required")
        if participation == "member_only":
            try:
                member_id = uuid.UUID(consumer_id)
            except ValueError as exc:
                raise ClaimEligibilityError("member_required") from exc
            member = await db.scalar(
                select(ConsumerProfile.id).where(
                    ConsumerProfile.id == member_id,
                    ConsumerProfile.tenant_id == tenant_id,
                )
            )
            if member is None:
                raise ClaimEligibilityError("member_required")
    elif (benefit.config_json or {}).get("validity_type") == "campaign_period":
        raise ClaimEligibilityError("campaign_required")

    validity_type = (benefit.config_json or {}).get("validity_type")
    if validity_type == "fixed_range":
        start_at = _aware_datetime(benefit.config_json.get("validity_start_at"))
        end_at = _aware_datetime(benefit.config_json.get("validity_end_at"))
        now = datetime.now(UTC)
        if start_at is None or end_at is None or not (start_at <= now < end_at):
            raise ClaimEligibilityError("benefit_not_eligible")
    return campaign
