"""Member notification preferences, inbox, and delivery orchestration."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import _session_uses_postgresql, callback_session_factory, set_session_tenant_context
from app.models.member import MemberIdentityCredential
from app.models.member_notification import (
    MemberChannelGrant,
    MemberNotification,
    MemberNotificationDelivery,
    MemberNotificationPreference,
)
from app.utils import utcnow

SUPPORTED_WECHAT_TEMPLATE_CODES = frozenset(
    {
        "coupon_expiry",
        "repurchase_invite",
        "gift_coupon",
        "coupon_issued",
        "order_paid",
        "order_fulfilled",
        "order_cancelled",
        "order_refunded",
    }
)
CHANNEL_GRANT_LIFETIME = timedelta(days=7)


async def _call(db: AsyncSession, function: str, tenant_id: uuid.UUID, payload: dict[str, Any]):
    try:
        return await db.scalar(
            text(f"SELECT public.{function}(:tenant_id,CAST(:payload AS jsonb))"),
            {"tenant_id": tenant_id, "payload": json.dumps(payload, separators=(",", ":"), default=str)},
        )
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        detail = str(exc.orig).splitlines()[0].split(": ", 1)[-1]
        raise HTTPException(status_code=403 if state == "42501" else 409, detail=detail) from exc


async def update_member_notification_preference(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    consumer_id: uuid.UUID,
    action: str,
    **values: Any,
) -> MemberNotificationPreference:
    await set_session_tenant_context(db, tenant_id)
    if action in {"subscribe_marketing", "grant_service_wechat"}:
        template_code = str(values.get("template_code", "")).strip()
        if template_code not in SUPPORTED_WECHAT_TEMPLATE_CODES:
            raise HTTPException(status_code=422, detail="notification_template_not_supported")
        template_id = settings.wechat_subscription_template_ids.get(template_code, "").strip()
        if not template_id:
            raise HTTPException(status_code=503, detail="wechat_subscription_template_not_configured")
        credential = await db.scalar(
            select(MemberIdentityCredential).where(
                MemberIdentityCredential.tenant_id == tenant_id,
                MemberIdentityCredential.membership_id == membership_id,
                MemberIdentityCredential.credential_type == "wechat_openid",
                MemberIdentityCredential.issuer == settings.shared_wechat_miniprogram_appid,
                MemberIdentityCredential.revoked_at.is_(None),
            )
        )
        if credential is None:
            raise HTTPException(status_code=409, detail="verified_wechat_identity_required")
        authorized_at = utcnow()
        values = {
            **values,
            "template_code": template_code,
            "authorization_ref": f"wechat:{credential.issuer}:{uuid7()}",
            "authorized_at": authorized_at,
            "expires_at": authorized_at + CHANNEL_GRANT_LIFETIME,
        }
    if _session_uses_postgresql(db):
        returned = await _call(
            db,
            "mutate_member_notification_preference_authority",
            tenant_id,
            {
                "action": action,
                "preference_id": uuid7(),
                "grant_id": uuid7(),
                "membership_id": membership_id,
                "consumer_id": consumer_id,
                **values,
            },
        )
        return (
            await db.scalars(
                select(MemberNotificationPreference)
                .where(MemberNotificationPreference.tenant_id == tenant_id, MemberNotificationPreference.id == returned)
                .execution_options(populate_existing=True)
            )
        ).one()

    preference = await db.scalar(
        select(MemberNotificationPreference).where(
            MemberNotificationPreference.tenant_id == tenant_id,
            MemberNotificationPreference.membership_id == membership_id,
        )
    )
    if preference is None:
        preference = MemberNotificationPreference(tenant_id=tenant_id, membership_id=membership_id)
        db.add(preference)
        await db.flush()
    if action == "subscribe_marketing":
        preference.marketing_enabled = True
        preference.marketing_consent_id = values["marketing_consent_id"]
        preference.marketing_opted_in_at = utcnow()
        preference.marketing_opted_out_at = None
        db.add(
            MemberChannelGrant(
                tenant_id=tenant_id,
                membership_id=membership_id,
                channel="wechat_subscription",
                template_code=values["template_code"],
                purpose="marketing",
                authorization_ref=values["authorization_ref"],
                status="available",
                authorized_at=values["authorized_at"],
                expires_at=values["expires_at"],
            )
        )
    elif action == "unsubscribe_marketing":
        preference.marketing_enabled = False
        preference.marketing_opted_out_at = utcnow()
    elif action == "set_service_wechat":
        preference.service_wechat_enabled = bool(values["enabled"])
    elif action == "grant_service_wechat":
        db.add(
            MemberChannelGrant(
                tenant_id=tenant_id,
                membership_id=membership_id,
                channel="wechat_subscription",
                template_code=values["template_code"],
                purpose="service",
                authorization_ref=values["authorization_ref"],
                status="available",
                authorized_at=values["authorized_at"],
                expires_at=values["expires_at"],
            )
        )
    else:
        raise HTTPException(status_code=422, detail="invalid_notification_preference_action")
    preference.version += 1
    await db.flush()
    return preference


async def list_member_notifications(
    db: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID, limit: int = 50
) -> list[MemberNotification]:
    await set_session_tenant_context(db, tenant_id)
    return list(
        (
            await db.scalars(
                select(MemberNotification)
                .where(
                    MemberNotification.tenant_id == tenant_id,
                    MemberNotification.membership_id == membership_id,
                )
                .order_by(MemberNotification.occurred_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_marketing_notification(
    db: AsyncSession, *, tenant_id: uuid.UUID, **payload: Any
) -> MemberNotification | None:
    await set_session_tenant_context(db, tenant_id)
    if not _session_uses_postgresql(db):
        preference = await get_member_notification_preference(
            db, tenant_id=tenant_id, membership_id=payload["membership_id"]
        )
        if preference is None or not preference.marketing_enabled:
            return None
        notification = MemberNotification(
            id=uuid7(),
            tenant_id=tenant_id,
            notification_class="marketing",
            **{
                key: payload[key]
                for key in (
                    "membership_id",
                    "notification_type",
                    "source_product",
                    "source_event_id",
                    "source_event_version",
                    "object_ref",
                    "title",
                    "body",
                    "action_path",
                    "facts",
                    "occurred_at",
                )
            },
        )
        db.add(notification)
        await db.flush()
        return notification
    notification_id = uuid7()
    returned = await _call(
        db,
        "create_member_marketing_notification_authority",
        tenant_id,
        {"notification_id": notification_id, "delivery_id": uuid7(), **payload},
    )
    if returned is None:
        return None
    return (
        await db.scalars(
            select(MemberNotification)
            .where(MemberNotification.tenant_id == tenant_id, MemberNotification.id == returned)
            .execution_options(populate_existing=True)
        )
    ).one()


async def get_member_notification_preference(
    db: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID
) -> MemberNotificationPreference | None:
    await set_session_tenant_context(db, tenant_id)
    return await db.scalar(
        select(MemberNotificationPreference).where(
            MemberNotificationPreference.tenant_id == tenant_id,
            MemberNotificationPreference.membership_id == membership_id,
        )
    )


async def record_delivery_result(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
    lease_token: uuid.UUID | None = None,
    **result: Any,
) -> MemberNotificationDelivery:
    await set_session_tenant_context(db, tenant_id)
    if not _session_uses_postgresql(db):
        delivery = await db.scalar(
            select(MemberNotificationDelivery).where(
                MemberNotificationDelivery.tenant_id == tenant_id,
                MemberNotificationDelivery.id == delivery_id,
            )
        )
        if delivery is None:
            raise HTTPException(status_code=404, detail="notification_delivery_not_found")
        delivery.attempt_count += 1
        if result["result"] == "accepted":
            delivery.status = "accepted"
            delivery.accepted_at = utcnow()
            delivery.channel_message_ref = result["channel_message_ref"]
        elif result["result"] == "permanent_failure" or delivery.attempt_count >= 3:
            delivery.status = "exhausted"
            delivery.exhausted_at = utcnow()
            delivery.last_error_code = result["error_code"]
        else:
            delivery.status = "failed"
            delivery.last_error_code = result["error_code"]
        await db.flush()
        return delivery
    try:
        async with callback_session_factory() as callback_db, callback_db.begin():
            await set_session_tenant_context(callback_db, tenant_id)
            await callback_db.scalar(
                text(
                    "SELECT public.mutate_member_notification_delivery_authority("
                    ":tenant_id,CAST(:payload AS jsonb))"
                ),
                {
                    "tenant_id": tenant_id,
                    "payload": json.dumps(
                        {"delivery_id": delivery_id, "lease_token": lease_token, **result},
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            )
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        detail = str(exc.orig).splitlines()[0].split(": ", 1)[-1]
        raise HTTPException(status_code=403 if state == "42501" else 409, detail=detail) from exc
    return (
        await db.scalars(
            select(MemberNotificationDelivery)
            .where(MemberNotificationDelivery.tenant_id == tenant_id, MemberNotificationDelivery.id == delivery_id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def lease_delivery(
    db: AsyncSession, *, tenant_id: uuid.UUID, delivery_id: uuid.UUID, lease_token: uuid.UUID
) -> bool:
    await set_session_tenant_context(db, tenant_id)
    if not _session_uses_postgresql(db):
        delivery = await db.scalar(
            select(MemberNotificationDelivery).where(
                MemberNotificationDelivery.tenant_id == tenant_id,
                MemberNotificationDelivery.id == delivery_id,
                MemberNotificationDelivery.status.in_(("pending", "failed")),
                MemberNotificationDelivery.next_attempt_at <= utcnow(),
            )
        )
        if delivery is None:
            return False
        delivery.status = "delivering"
        delivery.lease_token = lease_token
        delivery.lease_expires_at = utcnow() + timedelta(seconds=30)
        await db.flush()
        return True
    return bool(
        await db.scalar(
            text(
                "SELECT public.lease_member_notification_delivery_authority("
                ":tenant_id,:delivery_id,:lease_token)"
            ),
            {"tenant_id": tenant_id, "delivery_id": delivery_id, "lease_token": lease_token},
        )
    )
