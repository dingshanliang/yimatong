"""Open API 认证依赖 + 权限装饰器。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import ApiKey


async def authenticate_api_key(
    request: Request,
    db: AsyncSession,
) -> tuple[uuid.UUID, str, list[str]]:
    """验证 X-Api-Key header，返回 (tenant_id, role, permissions)。

    Raises HTTPException on failure.
    """
    api_key_str = request.headers.get("X-Api-Key", "")
    if not api_key_str:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    result = await db.execute(select(ApiKey).where(ApiKey.key == api_key_str, ApiKey.revoked.is_(False)))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    if key.expires_at and key.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="API key has expired")

    # 更新 last_used_at
    key.last_used_at = datetime.now(UTC)
    await db.flush()

    return key.tenant_id, key.role, key.permissions


def require_permission(permission: str):
    """FastAPI 依赖：检查当前请求的权限。

    用法: endpoint(..., permission_check: None = Depends(require_permission("scan:list")))
    """

    async def _check(request: Request) -> None:
        permissions: list[str] = getattr(request.state, "permissions", [])
        if permission not in permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return _check
