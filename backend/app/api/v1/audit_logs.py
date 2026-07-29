import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.audit import query_tenant_audit_logs
from app.utils.auth_rbac import require_role

audit_log_router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit-logs"])


class AuditOperatorRead(BaseModel):
    id: str
    name: str
    email: str | None = None


class TenantAuditLogRead(BaseModel):
    id: uuid.UUID
    tenant_id: str
    timestamp: datetime
    operator: AuditOperatorRead
    action: str
    resource: str
    result: str
    details: dict | None = None


class TenantAuditLogPage(BaseModel):
    items: list[TenantAuditLogRead]
    total: int
    page: int
    page_size: int


@audit_log_router.get("", response_model=TenantAuditLogPage, summary="查询租户操作日志")
async def list_tenant_audit_logs(
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    action: str | None = Query(None, max_length=100),
    keyword: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    result = await query_tenant_audit_logs(
        db,
        tenant_id,
        start_time=start_time,
        end_time=end_time,
        action=action,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    items = []
    for log in result["items"]:
        details = log.details or {}
        actor = result["actors"].get(log.operator_id)
        actor_name = actor.name if actor else details.get("actor_name") or log.operator_id
        actor_email = actor.email if actor else details.get("actor_email")
        items.append(
            TenantAuditLogRead(
                id=log.id,
                tenant_id=log.target_tenant_id,
                timestamp=log.timestamp,
                operator=AuditOperatorRead(id=log.operator_id, name=actor_name, email=actor_email),
                action=log.action,
                resource=log.resource,
                result=details.get("result", "success"),
                details=log.details,
            )
        )
    return TenantAuditLogPage(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )
