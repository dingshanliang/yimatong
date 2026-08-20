"""Consumer message center and restricted delivery-result APIs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.consumers import VerifiedConsumerScanContext, _resolve_scan_context, verify_consumer_scan_request
from app.core.database import get_db, get_db_for_consumer
from app.core.dependencies import get_current_tenant
from app.schemas.member_notification import (
    MarketingNotificationCreate,
    MarketingSubscriptionRequest,
    MemberNotificationItem,
    MemberNotificationPreferenceItem,
    ServiceChannelGrantRequest,
    ServiceChannelPreferenceRequest,
)
from app.services.brand_membership import get_brand_membership_for_profile
from app.services.channel_access import require_brand_channel_principal
from app.services.member_notification import (
    create_marketing_notification,
    get_member_notification_preference,
    list_member_notifications,
    update_member_notification_preference,
)
from app.utils.auth_rbac import require_permission

member_notification_router = APIRouter(prefix="/api/v1", tags=["member-notifications"])


async def _scope(scan_context: VerifiedConsumerScanContext, db: AsyncSession):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="active_membership_required")
    return tenant_id, consumer_id, membership["membership_id"]


def _preference(preference) -> MemberNotificationPreferenceItem:
    return MemberNotificationPreferenceItem(
        marketing_enabled=bool(preference and preference.marketing_enabled),
        service_wechat_enabled=True if preference is None else preference.service_wechat_enabled,
        critical_sms_enabled=bool(preference and preference.critical_sms_enabled),
        marketing_opted_in_at=preference.marketing_opted_in_at if preference else None,
        marketing_opted_out_at=preference.marketing_opted_out_at if preference else None,
    )


@member_notification_router.get("/consumers/membership/notifications", response_model=list[MemberNotificationItem])
async def notification_inbox_endpoint(
    limit: int = Query(default=50, ge=1, le=100),
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, _, membership_id = await _scope(scan_context, db)
    return await list_member_notifications(db, tenant_id=tenant_id, membership_id=membership_id, limit=limit)


@member_notification_router.get(
    "/consumers/membership/notification-preferences", response_model=MemberNotificationPreferenceItem
)
async def notification_preference_endpoint(
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, _, membership_id = await _scope(scan_context, db)
    return _preference(await get_member_notification_preference(db, tenant_id=tenant_id, membership_id=membership_id))


@member_notification_router.post(
    "/consumers/membership/notification-preferences/marketing-subscription",
    response_model=MemberNotificationPreferenceItem,
)
async def subscribe_marketing_endpoint(
    body: MarketingSubscriptionRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id, membership_id = await _scope(scan_context, db)
    return _preference(
        await update_member_notification_preference(
            db,
            tenant_id=tenant_id,
            membership_id=membership_id,
            consumer_id=consumer_id,
            action="subscribe_marketing",
            **body.model_dump(),
        )
    )


@member_notification_router.delete(
    "/consumers/membership/notification-preferences/marketing-subscription",
    response_model=MemberNotificationPreferenceItem,
)
async def unsubscribe_marketing_endpoint(
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id, membership_id = await _scope(scan_context, db)
    return _preference(
        await update_member_notification_preference(
            db,
            tenant_id=tenant_id,
            membership_id=membership_id,
            consumer_id=consumer_id,
            action="unsubscribe_marketing",
        )
    )


@member_notification_router.patch(
    "/consumers/membership/notification-preferences/service-wechat",
    response_model=MemberNotificationPreferenceItem,
)
async def service_wechat_preference_endpoint(
    body: ServiceChannelPreferenceRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id, membership_id = await _scope(scan_context, db)
    return _preference(
        await update_member_notification_preference(
            db,
            tenant_id=tenant_id,
            membership_id=membership_id,
            consumer_id=consumer_id,
            action="set_service_wechat",
            enabled=body.enabled,
        )
    )


@member_notification_router.post("/consumers/membership/notification-channel-grants")
async def service_channel_grant_endpoint(
    body: ServiceChannelGrantRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id, membership_id = await _scope(scan_context, db)
    await update_member_notification_preference(
        db,
        tenant_id=tenant_id,
        membership_id=membership_id,
        consumer_id=consumer_id,
        action="grant_service_wechat",
        **body.model_dump(),
    )
    return {"status": "authorized"}


@member_notification_router.post(
    "/member-notifications/marketing-events",
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("campaign:write"))],
)
async def marketing_event_endpoint(
    body: MarketingNotificationCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    notification = await create_marketing_notification(db, tenant_id=tenant_id, **body.model_dump())
    return {"created": notification is not None, "notification_id": notification.id if notification else None}
