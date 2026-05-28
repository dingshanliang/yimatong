"""任务状态查询 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.code_export import get_task_status

task_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@task_router.get("/{task_id}")
async def get_task_status_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    status = await get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status
