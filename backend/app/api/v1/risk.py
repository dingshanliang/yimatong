"""风险预警 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.risk import RiskAlert
from app.services.risk import freeze_code_item, list_risk_alerts, unfreeze_code_item

risk_router = APIRouter(prefix="/api/v1/risk-alerts", tags=["risk-alerts"])


class RiskAlertRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    alert_type: str
    public_id: str
    code_item_id: uuid.UUID
    detail: str
    ip_hash: str | None = None
    resolved: bool

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


@risk_router.get("")
async def list_risk_alerts_endpoint(
    alert_type: str | None = Query(None),
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    alerts, total = await list_risk_alerts(
        db, tenant_id, alert_type=alert_type, resolved=resolved,
        page=page, page_size=page_size,
    )
    return PaginatedResponse(
        items=[RiskAlertRead.model_validate(a) for a in alerts],
        total=total,
        page=page,
        page_size=page_size,
    )


@risk_router.post("/{alert_id}/resolve")
async def resolve_alert_endpoint(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from sqlalchemy import select

    result = await db.execute(
        select(RiskAlert).where(RiskAlert.id == alert_id, RiskAlert.tenant_id == tenant_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Risk alert not found")
    alert.resolved = True
    await db.commit()
    return RiskAlertRead.model_validate(alert)


@risk_router.post("/code-items/{item_id}/freeze")
async def freeze_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        item = await freeze_code_item(db, tenant_id, item_id)
        return {"id": str(item.id), "public_id": item.public_id, "status": item.status}
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@risk_router.post("/code-items/{item_id}/unfreeze")
async def unfreeze_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        item = await unfreeze_code_item(db, tenant_id, item_id)
        return {"id": str(item.id), "public_id": item.public_id, "status": item.status}
    except HTTPException:
        raise
