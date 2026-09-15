"""Durable worker for claim delivery intents."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB
from uuid6 import uuid7

logger = logging.getLogger(__name__)

WORKER_ID = "campaign-claim-worker-v1"
CALLBACK_TIMEOUT_SECONDS = 300
_OPERATIONAL_DELIVERY_STATUSES = {"pending", "processing", "success", "failed"}
_OPERATIONAL_DELIVERY_REASONS = {
    "ambiguous_provider_outcome",
    "provider_outcome_requires_reconciliation",
}


def _normalize_delivery_result(external_data: object) -> dict:
    """Bound provider output before it crosses the durable database seam."""

    if not isinstance(external_data, dict):
        return {}
    normalized: dict[str, object] = {}
    status = external_data.get("status")
    if status in _OPERATIONAL_DELIVERY_STATUSES:
        normalized["status"] = status
    status_code = external_data.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool) and 100 <= status_code <= 599:
        normalized["status_code"] = status_code
    reason = external_data.get("reason")
    if reason in _OPERATIONAL_DELIVERY_REASONS:
        normalized["reason"] = reason
    return normalized


async def _lease(db, tenant_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = (
        await db.execute(
            text("SELECT * FROM public.lease_campaign_claim_outbox(:tenant_id,:worker_id,:limit,60)"),
            {"tenant_id": tenant_id, "worker_id": WORKER_ID, "limit": limit},
        )
    ).mappings()
    return [dict(row) for row in rows.all()]


async def _complete(db, tenant_id: uuid.UUID, outbox_id: uuid.UUID, lease_token: uuid.UUID) -> None:
    await db.execute(
        text("SELECT * FROM public.complete_campaign_claim_outbox(:tenant_id,:outbox_id,:lease_token)"),
        {"tenant_id": tenant_id, "outbox_id": outbox_id, "lease_token": lease_token},
    )


async def _record_delivery_result(
    db,
    tenant_id: uuid.UUID,
    outbox_id: uuid.UUID,
    lease_token: uuid.UUID,
    delivery_id: uuid.UUID,
    connector_id: uuid.UUID,
    result_status: str,
    external_id: str,
    external_data: dict,
) -> dict:
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM public.record_campaign_claim_delivery_result("
                    ":tenant_id,:outbox_id,:lease_token,:delivery_id,:connector_id,:result_status,"
                    ":external_id,:external_data,:callback_timeout_seconds)"
                ).bindparams(bindparam("external_data", type_=JSONB)),
                {
                    "tenant_id": tenant_id,
                    "outbox_id": outbox_id,
                    "lease_token": lease_token,
                    "delivery_id": delivery_id,
                    "connector_id": connector_id,
                    "result_status": result_status,
                    "external_id": external_id,
                    "external_data": external_data,
                    "callback_timeout_seconds": CALLBACK_TIMEOUT_SECONDS,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _fail(
    db,
    tenant_id: uuid.UUID,
    outbox_id: uuid.UUID,
    lease_token: uuid.UUID,
    error_code: str,
    retry_seconds: int,
) -> None:
    await db.execute(
        text(
            "SELECT * FROM public.fail_campaign_claim_outbox("
            ":tenant_id,:outbox_id,:lease_token,:error_code,:retry_seconds)"
        ),
        {
            "tenant_id": tenant_id,
            "outbox_id": outbox_id,
            "lease_token": lease_token,
            "error_code": error_code,
            "retry_seconds": retry_seconds,
        },
    )


async def _consumer_with_scenario_consent(
    db, tenant_id: uuid.UUID, consumer_id: str, *, scenario: str, denied_reason: str
):
    """定位消费者档案并校验 scenario 级隐私同意，返回档案行。

    档案缺失、ID 非法或未授权时抛 LookupError（denied_reason 区分身份类别），
    走发放失败重试链；openid 与手机号两类身份解析共用本核心。
    """
    from app.models.consent import ConsentRecord, ConsentStatus, ConsentType
    from app.models.member import ConsumerProfile

    try:
        member_id = uuid.UUID(consumer_id)
    except ValueError:
        raise LookupError("consumer_profile_missing") from None
    member = await db.scalar(
        select(ConsumerProfile).where(
            ConsumerProfile.id == member_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
    )
    consent_status = None
    if member is not None:
        consent_status = await db.scalar(
            select(ConsentRecord.status)
            .where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.consumer_id == member_id,
                ConsentRecord.consent_type == ConsentType.privacy,
                ConsentRecord.scenario == scenario,
            )
            .order_by(ConsentRecord.granted_at.desc())
            .limit(1)
        )
    if member is None or consent_status != ConsentStatus.granted:
        raise LookupError(denied_reason)
    return member


async def _resolve_consumer_openid(db, tenant_id: uuid.UUID, consumer_id: str, *, scenario: str) -> str:
    """校验隐私同意并解密消费者 openid。

    未授权、无 openid 或档案缺失时抛 LookupError，走发放失败重试链；
    scenario 区分现金 payout 与权益发放两类同意场景。
    """
    from app.utils.crypto import decrypt_wechat_openid

    member = await _consumer_with_scenario_consent(
        db, tenant_id, consumer_id, scenario=scenario, denied_reason="open_id_consent_unavailable"
    )
    if not member.wechat_openid_ciphertext or not member.wechat_openid_nonce or not member.wechat_openid_key_id:
        raise LookupError("open_id_consent_unavailable")
    return decrypt_wechat_openid(
        tenant_id,
        member.id,
        member.wechat_openid_ciphertext,
        member.wechat_openid_nonce,
        member.wechat_openid_key_id,
    )


async def _resolve_consumer_phone(db, tenant_id: uuid.UUID, consumer_id: str, *, scenario: str) -> str:
    """校验隐私同意并解密消费者手机号（外部权益适配器如 weimob 以手机号桥接外部会员）。

    lead_contact_suppressed（隐私治理抑制）视同不可用；未授权、无手机号或
    档案缺失时抛 LookupError，走发放失败重试链。
    """
    from app.utils.crypto import decrypt_consumer_phone

    member = await _consumer_with_scenario_consent(
        db, tenant_id, consumer_id, scenario=scenario, denied_reason="phone_consent_unavailable"
    )
    if (
        member.lead_contact_suppressed
        or not member.phone_ciphertext
        or not member.phone_nonce
        or not member.phone_key_id
    ):
        raise LookupError("phone_consent_unavailable")
    return decrypt_consumer_phone(
        member.tenant_id,
        member.id,
        member.phone_ciphertext,
        member.phone_nonce,
        member.phone_key_id,
    )


async def _process_leased(tenant_id: uuid.UUID, leased: dict) -> bool:
    from app.core.database import async_session_factory, set_session_tenant_context
    from app.models.campaign import Benefit, BenefitClaim
    from app.models.connector import Connector
    from app.services.benefit_delivery_handler import _get_circuit_breaker
    from app.services.connectors import get_adapter
    from app.services.connectors.coupon_pool import CouponPoolAdapter
    from app.services.connectors.secrets import connector_with_runtime_secrets

    outbox_id = uuid.UUID(str(leased["outbox_id"]))
    claim_id = uuid.UUID(str(leased["claim_id"]))
    lease_token = uuid.UUID(str(leased["lease_token"]))
    attempt_count = int(leased["attempt_count"])
    max_attempts = int(leased["max_attempts"])
    try:
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            if leased.get("callback_timed_out") and attempt_count >= max_attempts:
                raise TimeoutError("delivery_callback_timeout")
            claim = await db.scalar(
                select(BenefitClaim).where(BenefitClaim.id == claim_id, BenefitClaim.tenant_id == tenant_id)
            )
            if claim is None:
                raise LookupError("claim_missing")
            benefit = await db.scalar(
                select(Benefit).where(Benefit.id == claim.benefit_id, Benefit.tenant_id == tenant_id)
            )
            if benefit is None:
                raise LookupError("benefit_missing")
            if benefit.connector_id is None:
                await _complete(db, tenant_id, outbox_id, lease_token)
                await db.commit()
                return True
            connector = await db.scalar(
                select(Connector).where(
                    Connector.id == benefit.connector_id,
                    Connector.tenant_id == tenant_id,
                    Connector.enabled.is_(True),
                )
            )
            if connector is None:
                raise LookupError("connector_unavailable")

            delivery_config = dict(benefit.config_json or {})
            delivery_config["benefit_type"] = benefit.benefit_type
            delivery_config["idempotency_key"] = str(claim.id)
            adapter = get_adapter(connector)
            if benefit.benefit_type == "cash_red_packet":
                if not claim.reserved_amount:
                    raise LookupError("cash_recipient_unavailable")
                openid = await _resolve_consumer_openid(db, tenant_id, claim.consumer_id, scenario="wechat_cash_payout")
                delivery_config.update(
                    {
                        "amount": claim.reserved_amount,
                        "openid": openid,
                        "out_bill_no": str(claim.id),
                        "transfer_remark": delivery_config.get("transfer_remark", "扫码领红包"),
                    }
                )
            elif getattr(adapter, "requires_openid", False):
                # 外部权益适配器（youzan 等）声明需要 openid 定位外部用户
                delivery_config["openid"] = await _resolve_consumer_openid(
                    db, tenant_id, claim.consumer_id, scenario="wechat_benefit_delivery"
                )
            elif getattr(adapter, "requires_phone", False):
                # 外部权益适配器（weimob 等）声明需要手机号桥接外部会员
                delivery_config["phone"] = await _resolve_consumer_phone(
                    db, tenant_id, claim.consumer_id, scenario="wechat_benefit_delivery"
                )

            circuit_breaker = _get_circuit_breaker(connector)
            if not circuit_breaker.is_available():
                raise ConnectionError("connector_circuit_open")
            # prepare 可能返回带刷新 token 的 transient 视图（不污染 ORM connector）
            prepared_connector = await adapter.prepare(db, connector)
            runtime_connector = connector_with_runtime_secrets(prepared_connector)
            if leased.get("callback_timed_out") and hasattr(adapter, "reconcile"):
                stable_provider_key = str(leased.get("external_id") or claim.id)
                result = await adapter.reconcile(runtime_connector, stable_provider_key)
            elif isinstance(adapter, CouponPoolAdapter):
                result = await adapter.deliver_from_pool(db, connector, claim.consumer_id, claim.id)
            else:
                result = await adapter.deliver(runtime_connector, claim.consumer_id, delivery_config)
            if result.status not in {"success", "pending"}:
                circuit_breaker.record_failure()
                raise RuntimeError("connector_delivery_failed")
            circuit_breaker.record_success()
            stable_external_id = str(delivery_config.get("out_bill_no") or result.external_id or claim.id)
            delivery_id = uuid.UUID(str(leased["delivery_id"])) if leased.get("delivery_id") else uuid7()
            await _record_delivery_result(
                db,
                tenant_id,
                outbox_id,
                lease_token,
                delivery_id,
                connector.id,
                result.status,
                stable_external_id,
                _normalize_delivery_result(result.external_data),
            )
            if result.status == "success":
                # 钩子失败绝不回滚发放结算：外部券停留 pending 由工作台兜底
                try:
                    await adapter.on_delivery_success(
                        db,
                        tenant_id=tenant_id,
                        claim_id=claim_id,
                        delivery_id=delivery_id,
                        external_id=stable_external_id,
                        consumer_id=claim.consumer_id,
                        benefit_config=delivery_config,
                    )
                except Exception as hook_exc:
                    logger.warning(
                        "Delivery success hook failed tenant=%s outbox=%s error=%s",
                        tenant_id,
                        outbox_id,
                        hook_exc,
                    )
            await db.commit()
            return True
    except Exception as exc:
        error_code = type(exc).__name__.lower()[:50]
        retry_seconds = min(3600, 2 ** min(attempt_count, 11))
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            await _fail(db, tenant_id, outbox_id, lease_token, error_code, retry_seconds)
            if attempt_count + 1 >= max_attempts:
                # 终态失败：把 pending 外部券钱包资产标记 error（无资产时 no-op）
                try:
                    from app.services.external_coupon_wallet import mark_external_coupon_sync_error_by_claim

                    await mark_external_coupon_sync_error_by_claim(
                        db, tenant_id=tenant_id, claim_id=claim_id, reason=error_code
                    )
                except Exception as mark_exc:
                    logger.warning(
                        "External coupon sync-error marking failed tenant=%s claim=%s error=%s",
                        tenant_id,
                        claim_id,
                        mark_exc,
                    )
            await db.commit()
        logger.warning(
            "Campaign claim delivery deferred tenant=%s outbox=%s error_code=%s",
            tenant_id,
            outbox_id,
            error_code,
        )
        return False


async def poll_campaign_claim_outbox() -> int:
    """Lease and process committed claim intents; Redis is not authoritative."""

    from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context
    from app.models.campaign import CampaignClaimOutbox

    async with async_session_factory() as control_db:
        tenant_rows = await bootstrap_tenant_keys(
            control_db,
            select(CampaignClaimOutbox.id, CampaignClaimOutbox.tenant_id)
            .where(CampaignClaimOutbox.status.in_(["pending", "processing", "awaiting_callback"]))
            .order_by(CampaignClaimOutbox.created_at, CampaignClaimOutbox.id)
            .limit(1000),
        )

    processed = 0
    tenant_ids = tuple(dict.fromkeys(tenant_id for _, tenant_id in tenant_rows))
    for tenant_id in tenant_ids:
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            leased = await _lease(db, tenant_id)
            await db.commit()
        for item in leased:
            await _process_leased(tenant_id, item)
            processed += 1
    return processed
