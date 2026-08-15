"""风险预警 API"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant, require_tenant_feature
from app.schemas.common import PaginatedResponse
from app.services.code import map_code_lifecycle_db_error
from app.services.risk import freeze_code_item, list_risk_alerts, resolve_risk_alert, unfreeze_code_item
from app.services.risk_access import risk_dependencies

risk_router = APIRouter(
    prefix="/api/v1/risk-alerts",
    tags=["risk-alerts"],
    dependencies=[Depends(require_tenant_feature("risk_module", db_scope="function"))],
)


class RiskAlertRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    alert_type: str
    public_id: str
    code_item_id: uuid.UUID
    detail: str
    ip_hash: str | None = None
    resolved: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CodeItemFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=200)
    confirm: Literal["freeze"]


def _raise_mapped_code_lifecycle_db_error(exc: DBAPIError) -> None:
    mapped = map_code_lifecycle_db_error(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


@risk_router.get("", summary="risk alerts 列表", dependencies=risk_dependencies("risk:read"))
async def list_risk_alerts_endpoint(
    alert_type: str | None = Query(None),
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    alerts, total = await list_risk_alerts(
        db,
        tenant_id,
        alert_type=alert_type,
        resolved=resolved,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[RiskAlertRead.model_validate(a) for a in alerts],
        total=total,
        page=page,
        page_size=page_size,
    )


@risk_router.post("/{alert_id}/resolve", summary="解析 alert", dependencies=risk_dependencies("risk:manage"))
async def resolve_alert_endpoint(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    alert = await resolve_risk_alert(db, tenant_id, alert_id, actor_id=str(account_id))
    return RiskAlertRead.model_validate(alert)


@risk_router.post("/code-items/{item_id}/freeze", dependencies=risk_dependencies("risk:manage"))
async def freeze_code_item_endpoint(
    item_id: uuid.UUID,
    body: CodeItemFreezeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=128),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    from app.services.code_state import InvalidStateTransitionError

    try:
        item = await freeze_code_item(
            db,
            tenant_id,
            item_id,
            actor_id=str(account_id),
            reason=body.reason,
            idempotency_key=idempotency_key,
        )
        return {"id": str(item.id), "public_id": item.public_id, "status": item.status}
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)


@risk_router.post("/code-items/{item_id}/unfreeze", dependencies=risk_dependencies("risk:manage"))
async def unfreeze_code_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    try:
        item = await unfreeze_code_item(db, tenant_id, item_id, actor_id=str(account_id))
        return {"id": str(item.id), "public_id": item.public_id, "status": item.status}
    except HTTPException:
        raise
    except DBAPIError as exc:
        _raise_mapped_code_lifecycle_db_error(exc)
