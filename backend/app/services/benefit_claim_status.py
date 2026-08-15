"""消费者侧权益发放状态派生：事实链（claim/outbox/delivery）→ 三态，只读。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_session_tenant_context
from app.models.campaign import BenefitClaim, CampaignClaimOutbox
from app.models.connector import BenefitDelivery
from app.services.benefit_claim_admission import build_claim_consumer_id
from app.services.claim_revisit_credential import verify_revisit_credential
from app.services.scan_token import verify_scan_token

CONSUMER_STATUS_PROCESSING = "processing"
CONSUMER_STATUS_SUCCESS = "success"
CONSUMER_STATUS_FAILED = "failed"

FAILURE_CHANNEL = "channel_failure"
FAILURE_RISK = "risk_paused"
FAILURE_RECIPIENT = "recipient_missing"
FAILURE_SYSTEM = "system_error"

# 领取受理只是意向事件；这些事实才构成"发放到账"的确认转化。
# not_required 表示无需异步投放（无 connector 权益），claim 本身即终态。
_CLAIM_DELIVERY_SUCCESS = {"success", "delivered", "not_required"}
_CLAIM_DELIVERY_FAILED = {"failed", "dead_letter"}
_OUTBOX_SUCCESS = {"delivered"}
_OUTBOX_FAILED = {"dead_letter"}
_CHANNEL_ERROR_MARKERS = ("connector_delivery_failed", "circuit", "timeout", "adapter")


@dataclass(frozen=True)
class ConsumerClaimStatus:
    status: str
    amount_minor: int | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None


def _classify_failure(outbox: CampaignClaimOutbox | None, delivery: BenefitDelivery | None) -> str:
    hints: list[str] = []
    if outbox is not None and outbox.last_error:
        hints.append(outbox.last_error)
    if delivery is not None and delivery.external_data:
        hints.append(str(delivery.external_data))
    text = " ".join(hints)
    if "cash_recipient_unavailable" in text:
        return FAILURE_RECIPIENT
    if "risk" in text:
        return FAILURE_RISK
    if any(marker in text for marker in _CHANNEL_ERROR_MARKERS):
        return FAILURE_CHANNEL
    return FAILURE_SYSTEM


def derive_consumer_claim_status(
    claim: BenefitClaim,
    outbox: CampaignClaimOutbox | None = None,
    delivery: BenefitDelivery | None = None,
) -> ConsumerClaimStatus:
    """从既有事实链派生消费者三态；终态单调，不回退、不并发行业务状态。"""

    outbox_dead = outbox is not None and outbox.status in _OUTBOX_FAILED
    legacy_delivery_dead = (
        delivery is not None
        and delivery.campaign_outbox_id is None
        and delivery.status == "failed"
        and delivery.retry_count >= delivery.max_retries
    )
    claim_dead = claim.delivery_status in _CLAIM_DELIVERY_FAILED

    if outbox_dead or legacy_delivery_dead or claim_dead:
        return ConsumerClaimStatus(
            status=CONSUMER_STATUS_FAILED,
            failure_reason=_classify_failure(outbox, delivery),
        )

    outbox_done = outbox is not None and outbox.status in _OUTBOX_SUCCESS
    claim_done = claim.delivery_status in _CLAIM_DELIVERY_SUCCESS
    if outbox_done or claim_done:
        completed_at = None
        if outbox is not None and outbox.delivered_at is not None:
            completed_at = outbox.delivered_at
        return ConsumerClaimStatus(
            status=CONSUMER_STATUS_SUCCESS,
            amount_minor=claim.reserved_amount,
            completed_at=completed_at,
        )

    return ConsumerClaimStatus(status=CONSUMER_STATUS_PROCESSING)


async def get_consumer_claim_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    expected_consumer_id: str,
) -> ConsumerClaimStatus | None:
    """按租户与领取者主体绑定查询；claim 不存在或主体不匹配统一返回 None（防枚举）。"""

    claim = await db.scalar(
        select(BenefitClaim).where(BenefitClaim.id == claim_id, BenefitClaim.tenant_id == tenant_id)
    )
    if claim is None or claim.consumer_id != expected_consumer_id:
        return None

    outbox = await db.scalar(
        select(CampaignClaimOutbox).where(
            CampaignClaimOutbox.tenant_id == tenant_id,
            CampaignClaimOutbox.claim_id == claim_id,
            CampaignClaimOutbox.event_type == "claim_committed",
        )
    )
    delivery = await db.scalar(
        select(BenefitDelivery)
        .where(
            BenefitDelivery.tenant_id == tenant_id,
            BenefitDelivery.claim_id == claim_id,
        )
        .order_by(BenefitDelivery.created_at.desc())
    )
    return derive_consumer_claim_status(claim, outbox, delivery)


async def resolve_consumer_claim_status(
    db: AsyncSession,
    token: str,
    claim_id: uuid.UUID,
) -> ConsumerClaimStatus | None:
    """Bearer 主体解析（scan_token 优先，回访凭证兜底）→ RLS 上下文 → 状态派生。

    token 无效、主体不可派生、凭证与该 claim 不绑定，任一情况统一返回 None，
    由端点映射为同一"不可查询"语义（防枚举）；不在本层区分失败原因。
    只读端点不校验 ip_hash：轮询跨网络切换不应中断，身份以 token 主体绑定为准。
    """

    token_payload = verify_scan_token(token)
    if token_payload is not None:
        try:
            tenant_id = uuid.UUID(str(token_payload.get("tenant_id")))
            consumer_id = build_claim_consumer_id(token_payload)
        except (TypeError, ValueError):
            return None
    else:
        credential = verify_revisit_credential(token, expected_claim_id=claim_id)
        if credential is None:
            return None
        try:
            tenant_id = uuid.UUID(str(credential.get("tenant_id")))
        except (TypeError, ValueError):
            return None
        consumer_id = credential["consumer_id"]

    tenant_id = await set_session_tenant_context(db, tenant_id)
    return await get_consumer_claim_status(db, tenant_id, claim_id, consumer_id)


async def build_claim_success_payload(
    db: AsyncSession,
    benefit,
    result: dict,
    consumer_id: str,
) -> dict:
    """领取受理成功响应：异步发放给 pending + 回访凭证，其余直接 claimed。

    幂等/重放命中时附上既有 claim 的真实三态（``delivery`` 字段）：领取受理
    只是意向事件，重放响应不得把已到终态的发放再次表述为纯 pending。
    回访凭证只绑定本笔 claim 与领取者主体；签发失败不阻断领取
    （轮询期内仍可用 scan_token）。
    """

    from app.services.claim_revisit_credential import issue_revisit_credential

    outcome = result.get("outcome", result.get("status"))
    replayed = outcome in {"idempotent", "replayed"}
    claim_id_raw = str(result.get("claim_id") or result.get("claim", {}).get("id", ""))
    payload: dict = {
        "status": "pending" if benefit.connector_id else "claimed",
        "benefit_id": str(benefit.id),
        "claim_id": claim_id_raw,
        "revisit_credential": None,
    }
    if not claim_id_raw:
        return payload
    try:
        claim_uuid = uuid.UUID(claim_id_raw)
    except ValueError:
        return payload
    try:
        payload["revisit_credential"] = issue_revisit_credential(benefit.tenant_id, claim_uuid, consumer_id)
    except ValueError:
        payload["revisit_credential"] = None
    if replayed and benefit.connector_id:
        derived = await get_consumer_claim_status(db, benefit.tenant_id, claim_uuid, consumer_id)
        if derived is not None:
            payload["delivery"] = {
                "status": derived.status,
                "amount_minor": derived.amount_minor,
                "completed_at": derived.completed_at.isoformat() if derived.completed_at else None,
                "failure_reason": derived.failure_reason,
            }
    return payload
