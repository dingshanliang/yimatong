"""旧版发放重试链终态失败的预留释放（kc6d.2）。"""

import uuid

import pytest

from app.models.campaign import Benefit, BenefitClaim
from app.services.benefit_delivery_handler import release_failed_delivery_reservation


def _seed_claim(db, *, reservation_status="reserved", claim_status="success"):
    tenant_id = uuid.uuid4()
    benefit = Benefit(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        campaign_id=uuid.uuid4(),
        name="现金红包",
        benefit_type="cash_red_packet",
        config_json={"claimed_budget": 100},
        stock_total=10,
        stock_used=1,
        status="active",
    )
    claim = BenefitClaim(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        benefit_id=benefit.id,
        consumer_id="anon:v1:abc",
        idempotency_key="claim:v1:d",
        claim_type="claim",
        status=claim_status,
        delivery_status="pending",
        reserved_amount=100,
        reservation_status=reservation_status,
    )
    db.add_all([benefit, claim])
    return tenant_id, benefit, claim


@pytest.mark.anyio
async def test_terminal_failure_releases_reservation_stock_and_budget(db):
    tenant_id, benefit, claim = _seed_claim(db)

    released = await release_failed_delivery_reservation(db, tenant_id, claim.id)
    await db.flush()

    assert released is True
    assert claim.status == "failed"
    assert claim.delivery_status == "failed"
    assert claim.reservation_status == "refunded"
    assert claim.reserved_amount == 100  # 金额保留作为事实，状态表达已退回
    assert benefit.stock_used == 0
    assert benefit.config_json["claimed_budget"] == 0


@pytest.mark.anyio
async def test_release_is_idempotent_for_already_released_claims(db):
    tenant_id, benefit, claim = _seed_claim(db)

    first = await release_failed_delivery_reservation(db, tenant_id, claim.id)
    second = await release_failed_delivery_reservation(db, tenant_id, claim.id)

    assert first is True
    assert second is False
    assert benefit.stock_used == 0  # 不重复回补


@pytest.mark.anyio
async def test_non_reserved_claim_is_untouched(db):
    tenant_id, benefit, claim = _seed_claim(db, reservation_status="not_required", claim_status="failed")
    claim.reserved_amount = None

    released = await release_failed_delivery_reservation(db, tenant_id, claim.id)

    assert released is False
    assert claim.reservation_status == "not_required"
    assert benefit.stock_used == 1


@pytest.mark.anyio
async def test_missing_budget_key_still_releases_claim(db):
    tenant_id, benefit, claim = _seed_claim(db)
    benefit.config_json = {}

    released = await release_failed_delivery_reservation(db, tenant_id, claim.id)

    assert released is True
    assert claim.reservation_status == "refunded"
    assert "claimed_budget" not in benefit.config_json
