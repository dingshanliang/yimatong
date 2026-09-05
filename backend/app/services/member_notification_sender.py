"""Lease and send due WeChat subscription deliveries through the trusted worker."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import exists, or_, select

from app.core.config import settings
from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context
from app.models.consent import ConsentRecord
from app.models.member import MemberIdentityCredential
from app.models.member_notification import MemberNotification, MemberNotificationDelivery, MemberNotificationPreference
from app.services.member_notification import lease_delivery, record_delivery_result
from app.utils.crypto import CryptoError, decrypt_member_identity_subject

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"
PERMANENT_WECHAT_ERRORS = {40003, 40037, 41030, 43101, 47003}


@dataclass(frozen=True)
class DeliverySnapshot:
    tenant_id: uuid.UUID
    delivery_id: uuid.UUID
    lease_token: uuid.UUID
    openid: str
    template_id: str
    page: str | None
    data: dict[str, dict[str, str]]


def _template_data(notification: MemberNotification) -> dict[str, dict[str, str]]:
    configured = notification.facts.get("wechat_template_data") if isinstance(notification.facts, dict) else None
    if isinstance(configured, dict) and configured:
        return {
            str(key): {"value": str(value.get("value", "") if isinstance(value, dict) else value)}
            for key, value in configured.items()
        }
    return {"thing1": {"value": notification.title[:20]}, "thing2": {"value": notification.body[:20]}}


async def _snapshot_delivery(
    tenant_id: uuid.UUID, delivery_id: uuid.UUID, lease_token: uuid.UUID
) -> DeliverySnapshot | None:
    async with async_session_factory() as db, db.begin():
        await set_session_tenant_context(db, tenant_id)
        active_marketing_consent = exists(
            select(1)
            .select_from(MemberNotificationPreference)
            .join(
                ConsentRecord,
                (ConsentRecord.tenant_id == MemberNotificationPreference.tenant_id)
                & (ConsentRecord.id == MemberNotificationPreference.marketing_consent_id),
            )
            .where(
                MemberNotificationPreference.tenant_id == MemberNotification.tenant_id,
                MemberNotificationPreference.membership_id == MemberNotification.membership_id,
                MemberNotificationPreference.marketing_enabled.is_(True),
                MemberNotificationPreference.marketing_opted_out_at.is_(None),
                ConsentRecord.status == "granted",
                ConsentRecord.consent_type == "marketing",
                ConsentRecord.purpose.in_(("marketing", "lead_capture")),
                ConsentRecord.withdrawn_at.is_(None),
            )
        )
        row = (
            await db.execute(
                select(MemberNotificationDelivery, MemberNotification)
                .join(
                    MemberNotification,
                    (MemberNotification.tenant_id == MemberNotificationDelivery.tenant_id)
                    & (MemberNotification.id == MemberNotificationDelivery.notification_id),
                )
                .where(
                    MemberNotificationDelivery.tenant_id == tenant_id,
                    MemberNotificationDelivery.id == delivery_id,
                    MemberNotificationDelivery.status == "delivering",
                    MemberNotificationDelivery.lease_token == lease_token,
                    or_(MemberNotification.notification_class != "marketing", active_marketing_consent),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        delivery, notification = row
        credential = await db.scalar(
            select(MemberIdentityCredential).where(
                MemberIdentityCredential.tenant_id == tenant_id,
                MemberIdentityCredential.membership_id == notification.membership_id,
                MemberIdentityCredential.credential_type == "wechat_openid",
                MemberIdentityCredential.issuer == settings.shared_wechat_miniprogram_appid,
                MemberIdentityCredential.revoked_at.is_(None),
            )
        )
        template_id = settings.wechat_subscription_template_ids.get(delivery.template_code, "").strip()
        if credential is None or not template_id:
            return DeliverySnapshot(
                tenant_id,
                delivery_id,
                lease_token,
                "",
                template_id,
                notification.action_path,
                _template_data(notification),
            )
        try:
            openid = decrypt_member_identity_subject(
                tenant_id,
                notification.membership_id,
                credential.credential_type,
                credential.issuer,
                credential.subject_ciphertext,
                credential.subject_nonce,
                credential.subject_key_id,
            )
        except CryptoError:
            openid = ""
        return DeliverySnapshot(
            tenant_id,
            delivery_id,
            lease_token,
            openid,
            template_id,
            notification.action_path,
            _template_data(notification),
        )


async def _send(snapshot: DeliverySnapshot) -> tuple[str, str | None, str | None]:
    if not snapshot.openid:
        return "permanent_failure", None, "wechat_recipient_unavailable"
    if not snapshot.template_id:
        return "permanent_failure", None, "wechat_template_not_configured"
    if not settings.shared_wechat_miniprogram_secret.strip():
        return "transient_failure", None, "wechat_credentials_not_configured"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            token_response = await client.get(
                TOKEN_URL,
                params={
                    "grant_type": "client_credential",
                    "appid": settings.shared_wechat_miniprogram_appid,
                    "secret": settings.shared_wechat_miniprogram_secret,
                },
            )
            token_response.raise_for_status()
            access_token = str(token_response.json().get("access_token", ""))
            if not access_token:
                return "transient_failure", None, "wechat_access_token_unavailable"
            response = await client.post(
                SEND_URL,
                params={"access_token": access_token},
                json={
                    "touser": snapshot.openid,
                    "template_id": snapshot.template_id,
                    "page": snapshot.page.lstrip("/") if snapshot.page else None,
                    "data": snapshot.data,
                    "miniprogram_state": "formal" if settings.environment == "production" else "developer",
                    "lang": "zh_CN",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return "transient_failure", None, "wechat_transport_failure"
    error_code = int(payload.get("errcode", -1))
    if error_code == 0:
        return "accepted", str(payload.get("msgid") or f"wechat:{snapshot.delivery_id}"), None
    result = "permanent_failure" if error_code in PERMANENT_WECHAT_ERRORS else "transient_failure"
    return result, None, f"wechat_{error_code}"


async def process_member_notification_delivery(tenant_id: uuid.UUID, delivery_id: uuid.UUID) -> bool:
    lease_token = uuid.uuid4()
    async with async_session_factory() as db, db.begin():
        if not await lease_delivery(db, tenant_id=tenant_id, delivery_id=delivery_id, lease_token=lease_token):
            return False
    snapshot = await _snapshot_delivery(tenant_id, delivery_id, lease_token)
    if snapshot is None:
        return False
    result, channel_ref, error_code = await _send(snapshot)
    async with async_session_factory() as db, db.begin():
        await record_delivery_result(
            db,
            tenant_id=tenant_id,
            delivery_id=delivery_id,
            lease_token=lease_token,
            result=result,
            channel_message_ref=channel_ref,
            error_code=error_code,
        )
    return True


async def poll_member_notification_deliveries(limit: int = 100) -> int:
    now = datetime.now(UTC)
    async with async_session_factory() as db:
        keys = await bootstrap_tenant_keys(
            db,
            select(MemberNotificationDelivery.id, MemberNotificationDelivery.tenant_id)
            .where(
                or_(
                    (
                        MemberNotificationDelivery.status.in_(("pending", "failed"))
                        & (MemberNotificationDelivery.next_attempt_at <= now)
                    ),
                    (
                        (MemberNotificationDelivery.status == "delivering")
                        & (MemberNotificationDelivery.lease_expires_at <= now)
                    ),
                )
            )
            .order_by(MemberNotificationDelivery.next_attempt_at, MemberNotificationDelivery.id)
            .limit(limit),
        )
    processed = 0
    for delivery_id, tenant_id in keys:
        processed += int(await process_member_notification_delivery(tenant_id, delivery_id))
    return processed
