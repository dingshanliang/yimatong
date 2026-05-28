"""代运营工作台 API"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.ops import get_launch_checklist, get_tenant_status

ops_router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


@ops_router.get("/clients/{tenant_id}/status")
async def get_client_status(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_tenant: uuid.UUID = Depends(get_current_tenant),
):
    return await get_tenant_status(db, tenant_id)


@ops_router.get("/clients/{tenant_id}/launch-checklist")
async def get_launch_checklist_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_tenant: uuid.UUID = Depends(get_current_tenant),
):
    return await get_launch_checklist(db, tenant_id)
