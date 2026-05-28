"""公开页面配置端点（H5 获取页面 DSL）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.page import PageVersion

public_page_router = APIRouter(tags=["public-pages"])


@public_page_router.get("/api/v1/public/pages/{version_id}")
async def get_public_page(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """H5 获取页面配置 DSL（公开端点，无需认证）"""
    result = await db.execute(
        select(PageVersion).where(PageVersion.id == version_id)
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Page version not found")
    return {
        "id": str(version.id),
        "version": version.version,
        "config_json": version.config_json,
        "status": version.status,
    }
