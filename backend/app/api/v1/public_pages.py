"""公开页面配置端点（H5 获取页面 DSL）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.launch import LaunchRelease, LaunchReleaseStatus
from app.models.page import PageVersion, PageVersionStatus
from app.utils.auth_rbac import require_role

public_page_router = APIRouter(tags=["public-pages"])


@public_page_router.get("/api/v1/public/pages/{version_id}")
async def get_public_page(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    """Admin preview client reads a published page DSL in its tenant context.

    安全约束：
    - requires tenant authentication and only returns that tenant's published version
    - 不暴露 tenant_id 等内部信息
    """
    suspended = await db.scalar(
        select(LaunchRelease.id).where(
            LaunchRelease.tenant_id == tenant_id,
            LaunchRelease.page_version_id == version_id,
            LaunchRelease.status == LaunchReleaseStatus.suspended,
        )
    )
    if suspended:
        raise HTTPException(status_code=409, detail="该上线版本已暂停，消费者暂时无法访问")
    result = await db.execute(
        select(PageVersion).where(
            PageVersion.id == version_id,
            PageVersion.tenant_id == tenant_id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Page version not found")

    return {
        "id": str(version.id),
        "version": version.version,
        "config_json": version.config_json,
    }
