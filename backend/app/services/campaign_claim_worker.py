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


async def _process_leased(tenant_id: uuid.UUID, leased: dict) -> bool:
    from app.core.database import async_session_factory, set_session_tenant_context
    from app.models.campaign import Benefit, BenefitClaim
    from app.models.connector import Connector
    from app.models.consent import ConsentRecord, ConsentStatus, ConsentType
    from app.models.member import ConsumerProfile
    from app.services.benefit_delivery_handler import _get_circuit_breaker
    from app.services.connectors import get_adapter
    from app.services.connectors.coupon_pool import CouponPoolAdapter
    from app.services.connectors.secrets import connector_with_runtime_secrets
    from app.utils.crypto import decrypt_wechat_openid

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
            if benefit.benefit_type == "cash_red_packet":
                member = None
                try:
                    member_id = uuid.UUID(claim.consumer_id)
                except ValueError:
                    member_id = None
                if member_id is not None:
                    member = await db.scalar(
                        select(ConsumerProfile).where(
                            ConsumerProfile.id == member_id,
                            ConsumerProfile.tenant_id == tenant_id,
                        )
                    )
                consent_status = None
                if member_id is not None:
                    consent_status = await db.scalar(
                        select(ConsentRecord.status)
                        .where(
                            ConsentRecord.tenant_id == tenant_id,
                            ConsentRecord.consumer_id == member_id,
                            ConsentRecord.consent_type == ConsentType.privacy,
                            ConsentRecord.scenario == "wechat_cash_payout",
                        )
                        .order_by(ConsentRecord.granted_at.desc())
                        .limit(1)
                    )
                if (
                    member is None
                    or consent_status != ConsentStatus.granted
                    or not member.wechat_openid_ciphertext
                    or not member.wechat_openid_nonce
                    or not member.wechat_openid_key_id
                    or not claim.reserved_amount
                ):
                    raise LookupError("cash_recipient_unavailable")
                openid = decrypt_wechat_openid(
                    tenant_id,
                    member.id,
                    member.wechat_openid_ciphertext,
                    member.wechat_openid_nonce,
                    member.wechat_openid_key_id,
                )
                delivery_config.update(
                    {
                        "amount": claim.reserved_amount,
                        "openid": openid,
                        "out_bill_no": str(claim.id),
                        "transfer_remark": delivery_config.get("transfer_remark", "扫码领红包"),
                    }
                )

            circuit_breaker = _get_circuit_breaker(connector)
            if not circuit_breaker.is_available():
                raise ConnectionError("connector_circuit_open")
            runtime_connector = connector_with_runtime_secrets(connector)
            adapter = get_adapter(runtime_connector)
            if isinstance(adapter, CouponPoolAdapter):
                result = await adapter.deliver_from_pool(db, connector, claim.consumer_id, claim.id)
            else:
                result = await adapter.deliver(runtime_connector, claim.consumer_id, delivery_config)
            if result.status not in {"success", "pending"}:
                circuit_breaker.record_failure()
                raise RuntimeError("connector_delivery_failed")
            circuit_breaker.record_success()
            stable_external_id = str(delivery_config.get("out_bill_no") or result.external_id or claim.id)
            await _record_delivery_result(
                db,
                tenant_id,
                outbox_id,
                lease_token,
                uuid.UUID(str(leased["delivery_id"])) if leased.get("delivery_id") else uuid7(),
                connector.id,
                result.status,
                stable_external_id,
                result.external_data if isinstance(result.external_data, dict) else {},
            )
            await db.commit()
            return True
    except Exception as exc:
        error_code = type(exc).__name__.lower()[:50]
        retry_seconds = min(3600, 2 ** min(attempt_count, 11))
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            await _fail(db, tenant_id, outbox_id, lease_token, error_code, retry_seconds)
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
