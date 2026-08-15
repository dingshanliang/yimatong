"""领取受理成功 payload：回访凭证签发 + 幂等重放附带既有 claim 的真实三态。"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim, CampaignClaimOutbox
from app.services.benefit_claim_status import build_claim_success_payload
from app.services.claim_revisit_credential import verify_revisit_credential
from tests.conftest import TestSessionLocal


def _benefit(tenant_id: uuid.UUID, *, connector: bool = True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        connector_id=uuid.uuid4() if connector else None,
    )


async def _seed_claim(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: str,
    *,
    outbox_status: str = "dead_letter",
) -> uuid.UUID:
    claim_id = uuid.uuid4()
    session.add(
        BenefitClaim(
            id=claim_id,
            tenant_id=tenant_id,
            benefit_id=uuid.uuid4(),
            consumer_id=consumer_id,
            idempotency_key=f"claim:v1:{uuid.uuid4().hex}",
            claim_type="claim",
            status="success",
            delivery_status="pending",
            reserved_amount=66,
            reservation_status="reserved",
        )
    )
    session.add(
        CampaignClaimOutbox(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            claim_id=claim_id,
            event_type="claim_committed",
            payload={},
            status=outbox_status,
            attempt_count=8,
            max_attempts=8,
            last_error="connector_delivery_failed" if outbox_status == "dead_letter" else None,
            delivered_at=datetime.now(UTC) if outbox_status == "delivered" else None,
        )
    )
    await session.commit()
    return claim_id


@pytest.mark.anyio
async def test_fresh_connector_claim_is_pending_with_bound_credential():
    """新领取（意向事件）：status=pending；凭证签发且绑定本笔 claim。"""

    tenant_id = uuid.uuid4()
    consumer_id = f"anon:v1:{uuid.uuid4().hex}"
    claim_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        payload = await build_claim_success_payload(
            session,
            _benefit(tenant_id),
            {"outcome": "success", "claim_id": str(claim_id)},
            consumer_id,
        )

    assert payload["status"] == "pending"
    assert payload["claim_id"] == str(claim_id)
    assert "delivery" not in payload
    credential = verify_revisit_credential(payload["revisit_credential"], expected_claim_id=claim_id)
    assert credential is not None and credential["consumer_id"] == consumer_id


@pytest.mark.anyio
async def test_no_connector_claim_is_claimed():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        payload = await build_claim_success_payload(
            session,
            _benefit(tenant_id, connector=False),
            {"outcome": "success", "claim_id": str(uuid.uuid4())},
            f"anon:v1:{uuid.uuid4().hex}",
        )

    assert payload["status"] == "claimed"


@pytest.mark.anyio
async def test_replayed_claim_carries_real_failed_state_not_pending_only():
    """幂等重放（Story 22）：事实链已是 dead_letter 时，响应必须带真实 failed 三态。"""

    tenant_id = uuid.uuid4()
    consumer_id = f"anon:v1:{uuid.uuid4().hex}"
    async with TestSessionLocal() as session:
        claim_id = await _seed_claim(session, tenant_id, consumer_id)
        payload = await build_claim_success_payload(
            session,
            _benefit(tenant_id),
            {"outcome": "replayed", "claim_id": str(claim_id)},
            consumer_id,
        )

    assert payload["status"] == "pending"  # 受理口径：仍引导进入结果页
    assert payload["delivery"]["status"] == "failed"
    assert payload["delivery"]["failure_reason"] == "channel_failure"
    assert payload["delivery"]["amount_minor"] is None


@pytest.mark.anyio
async def test_replayed_claim_carries_real_success_state():
    tenant_id = uuid.uuid4()
    consumer_id = f"anon:v1:{uuid.uuid4().hex}"
    async with TestSessionLocal() as session:
        claim_id = await _seed_claim(session, tenant_id, consumer_id, outbox_status="delivered")
        payload = await build_claim_success_payload(
            session,
            _benefit(tenant_id),
            {"status": "idempotent", "claim": {"id": str(claim_id)}},  # SQLite 服务路径的回放形状
            consumer_id,
        )

    assert payload["delivery"]["status"] == "success"
    assert payload["delivery"]["amount_minor"] == 66


@pytest.mark.anyio
async def test_malformed_claim_id_never_blocks_receipt():
    """claim_id 不可解析时：凭证缺席但不阻断领取响应。"""

    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        payload = await build_claim_success_payload(
            session,
            _benefit(tenant_id),
            {"outcome": "replayed", "claim_id": "not-a-uuid"},
            f"anon:v1:{uuid.uuid4().hex}",
        )

    assert payload["status"] == "pending"
    assert payload["revisit_credential"] is None
    assert "delivery" not in payload
