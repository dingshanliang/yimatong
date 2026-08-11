"""Metadata contract for tenant-bound campaign authority and claim outbox."""

from sqlalchemy import DateTime

from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignClaimOutbox
from app.models.connector import BenefitDelivery
from app.models.product import Product  # noqa: F401 - resolve composite FK metadata


def _foreign_key_columns(table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    }


def test_campaign_authority_metadata_is_tenant_composite_and_time_typed() -> None:
    assert isinstance(Campaign.__table__.c.start_at.type, DateTime)
    assert Campaign.__table__.c.start_at.type.timezone is True
    assert isinstance(Campaign.__table__.c.end_at.type, DateTime)
    assert Campaign.__table__.c.end_at.type.timezone is True

    assert (("tenant_id", "product_id"), ("products.tenant_id", "products.id")) in _foreign_key_columns(
        Campaign.__table__
    )
    assert (("tenant_id", "campaign_id"), ("campaigns.tenant_id", "campaigns.id")) in _foreign_key_columns(
        Benefit.__table__
    )
    claim_fks = _foreign_key_columns(BenefitClaim.__table__)
    assert (("tenant_id", "benefit_id"), ("benefits.tenant_id", "benefits.id")) in claim_fks
    assert (("tenant_id", "campaign_id"), ("campaigns.tenant_id", "campaigns.id")) in claim_fks


def test_claim_outbox_is_tenant_bound_and_has_retry_indexes() -> None:
    outbox = CampaignClaimOutbox.__table__
    assert (("tenant_id", "claim_id"), ("benefit_claims.tenant_id", "benefit_claims.id")) in _foreign_key_columns(
        outbox
    )
    assert {index.name for index in outbox.indexes} >= {
        "ix_campaign_claim_outbox_tenant_status",
        "ix_campaign_claim_outbox_ready",
        "ix_campaign_claim_outbox_expired_lease",
    }
    assert {constraint.name for constraint in outbox.constraints} >= {
        "uq_campaign_claim_outbox_claim_event",
        "ck_campaign_claim_outbox_status",
        "ck_campaign_claim_outbox_lease",
        "ck_campaign_claim_outbox_delivery",
    }


def test_authoritative_benefit_delivery_is_tenant_bound_and_unique() -> None:
    delivery = BenefitDelivery.__table__
    delivery_fks = _foreign_key_columns(delivery)
    assert (("tenant_id", "claim_id"), ("benefit_claims.tenant_id", "benefit_claims.id")) in delivery_fks
    assert (
        ("tenant_id", "campaign_outbox_id"),
        ("campaign_claim_outbox.tenant_id", "campaign_claim_outbox.id"),
    ) in delivery_fks
    assert {index.name for index in delivery.indexes} >= {"uq_benefit_deliveries_authority_claim"}
    assert {constraint.name for constraint in delivery.constraints} >= {
        "uq_benefit_deliveries_tenant_outbox",
        "ck_benefit_deliveries_authority_identity",
    }
