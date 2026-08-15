"""消费者发放状态派生：事实链（claim/outbox/delivery）→ 三态映射表测试。"""

import uuid
from datetime import UTC, datetime

from app.models.campaign import BenefitClaim, CampaignClaimOutbox
from app.models.connector import BenefitDelivery
from app.services.benefit_claim_status import (
    derive_consumer_claim_status,
)


def _claim(**overrides) -> BenefitClaim:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "benefit_id": uuid.uuid4(),
        "consumer_id": "anon:v1:abc",
        "idempotency_key": "claim:v1:digest",
        "claim_type": "claim",
        "status": "success",
        "delivery_status": "pending",
        "reserved_amount": 88,
        "reservation_status": "reserved",
    }
    base.update(overrides)
    return BenefitClaim(**base)


def _outbox(claim: BenefitClaim, **overrides) -> CampaignClaimOutbox:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": claim.tenant_id,
        "claim_id": claim.id,
        "event_type": "claim_committed",
        "payload": {},
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": 8,
    }
    base.update(overrides)
    return CampaignClaimOutbox(**base)


def _delivery(claim: BenefitClaim, **overrides) -> BenefitDelivery:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": claim.tenant_id,
        "connector_id": uuid.uuid4(),
        "benefit_id": claim.benefit_id,
        "claim_id": claim.id,
        "consumer_id": claim.consumer_id,
        "benefit_type": "cash_red_packet",
        "benefit_config": {},
        "status": "pending",
        "retry_count": 0,
        "max_retries": 5,
    }
    base.update(overrides)
    return BenefitDelivery(**base)


class TestProcessing:
    def test_fresh_cash_claim_is_processing(self):
        assert derive_consumer_claim_status(_claim()).status == "processing"

    def test_outbox_retrying_states_are_processing(self):
        claim = _claim()
        for status in ("pending", "processing", "awaiting_callback"):
            result = derive_consumer_claim_status(claim, _outbox(claim, status=status))
            assert result.status == "processing", status

    def test_legacy_delivery_failed_but_retries_left_is_processing(self):
        claim = _claim()
        delivery = _delivery(claim, status="failed", retry_count=3, max_retries=5)
        assert derive_consumer_claim_status(claim, None, delivery).status == "processing"


class TestSuccess:
    def test_outbox_delivered_with_settled_claim_is_success_with_amount(self):
        claim = _claim(delivery_status="success")
        delivered_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
        outbox = _outbox(claim, status="delivered", delivered_at=delivered_at)
        result = derive_consumer_claim_status(claim, outbox)
        assert result.status == "success"
        assert result.amount_minor == 88
        assert result.completed_at == delivered_at

    def test_claim_delivery_delivered_without_outbox_is_success(self):
        claim = _claim(delivery_status="delivered")
        result = derive_consumer_claim_status(claim)
        assert result.status == "success"
        assert result.failure_reason is None

    def test_not_required_delivery_is_terminal_success_without_amount(self):
        claim = _claim(delivery_status="not_required", reserved_amount=None, reservation_status="not_required")
        assert derive_consumer_claim_status(claim).status == "success"


class TestFailed:
    def test_outbox_dead_letter_channel_error_is_failed_channel(self):
        claim = _claim()
        outbox = _outbox(
            claim,
            status="dead_letter",
            attempt_count=8,
            last_error="connector_delivery_failed: wechat transfer rejected",
        )
        result = derive_consumer_claim_status(claim, outbox)
        assert result.status == "failed"
        assert result.failure_reason == "channel_failure"
        assert result.amount_minor is None

    def test_dead_letter_recipient_unavailable_is_failed_recipient(self):
        claim = _claim()
        outbox = _outbox(claim, status="dead_letter", last_error="cash_recipient_unavailable")
        result = derive_consumer_claim_status(claim, outbox)
        assert result.status == "failed"
        assert result.failure_reason == "recipient_missing"

    def test_dead_letter_risk_signal_is_failed_risk(self):
        claim = _claim()
        outbox = _outbox(claim, status="dead_letter", last_error="risk control paused payout")
        result = derive_consumer_claim_status(claim, outbox)
        assert result.status == "failed"
        assert result.failure_reason == "risk_paused"

    def test_dead_letter_unknown_error_is_failed_system(self):
        claim = _claim()
        outbox = _outbox(claim, status="dead_letter", last_error="boom")
        result = derive_consumer_claim_status(claim, outbox)
        assert result.status == "failed"
        assert result.failure_reason == "system_error"

    def test_legacy_delivery_exhausted_retries_is_failed(self):
        claim = _claim()
        delivery = _delivery(claim, status="failed", retry_count=5, max_retries=5)
        result = derive_consumer_claim_status(claim, None, delivery)
        assert result.status == "failed"

    def test_claim_delivery_failed_is_failed(self):
        claim = _claim(delivery_status="failed")
        result = derive_consumer_claim_status(claim)
        assert result.status == "failed"


class TestMonotonicTerminal:
    def test_failed_terminal_wins_over_stale_pending_side_facts(self):
        """终态单调：dead_letter 不被另一侧未更新的 pending 事实拉回 processing。"""

        claim = _claim(delivery_status="pending")
        outbox = _outbox(claim, status="dead_letter", last_error="connector_delivery_failed")
        assert derive_consumer_claim_status(claim, outbox).status == "failed"

    def test_success_terminal_wins_over_stale_delivery_pending(self):
        claim = _claim(delivery_status="success")
        outbox = _outbox(claim, status="delivered")
        delivery = _delivery(claim, status="pending")
        assert derive_consumer_claim_status(claim, outbox, delivery).status == "success"
