"""风控通知 API"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant, require_tenant_feature
from app.models.risk import RiskNotification
from app.schemas.common import PaginatedResponse
from app.services.risk_access import risk_dependencies
from app.services.risk_authority import mark_all_notifications_read, mark_notification_read

risk_notification_router = APIRouter(
    prefix="/api/v1/risk-notifications",
    tags=["risk-notifications"],
    dependencies=[Depends(require_tenant_feature("risk_module", db_scope="function"))],
)


class RiskNotificationRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    notification_type: str
    title: str
    detail: str
    risk_rule_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    code_item_id: uuid.UUID | None = None
    read: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


@risk_notification_router.get("", summary="风控通知列表", dependencies=risk_dependencies("risk:read"))
async def list_notifications(
    notification_type: str | None = Query(None),
    read: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    stmt = select(RiskNotification).where(RiskNotification.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(RiskNotification).where(RiskNotification.tenant_id == tenant_id)

    if notification_type:
        stmt = stmt.where(RiskNotification.notification_type == notification_type)
        count_stmt = count_stmt.where(RiskNotification.notification_type == notification_type)
    if read is not None:
        stmt = stmt.where(RiskNotification.read == read)
        count_stmt = count_stmt.where(RiskNotification.read == read)

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(RiskNotification.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)

    return PaginatedResponse(
        items=[RiskNotificationRead.model_validate(n) for n in result.scalars().all()],
        total=total,
        page=page,
        page_size=page_size,
    )


@risk_notification_router.get("/unread-count", summary="未读通知数量", dependencies=risk_dependencies("risk:read"))
async def unread_count(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(
        select(func.count())
        .select_from(RiskNotification)
        .where(RiskNotification.tenant_id == tenant_id, RiskNotification.read.is_(False))
    )
    return {"unread_count": result.scalar() or 0}


@risk_notification_router.post(
    "/{notification_id}/read", summary="标记通知已读", dependencies=risk_dependencies("risk:manage")
)
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    result = await mark_notification_read(
        db,
        tenant_id,
        notification_id=notification_id,
        idempotency_key=idempotency_key,
    )
    return {"id": str(result["notification_id"]), "read": result["read"]}


@risk_notification_router.post("/mark-all-read", summary="标记全部已读", dependencies=risk_dependencies("risk:manage"))
async def mark_all_read(
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    result = await mark_all_notifications_read(db, tenant_id, idempotency_key=idempotency_key)
    return {"updated": result["updated_count"]}
