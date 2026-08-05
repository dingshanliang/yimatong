"""Open API 认证依赖 + 权限装饰器。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import bootstrap_tenant_row
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

    key = await bootstrap_tenant_row(
        db,
        select(ApiKey).where(ApiKey.key == api_key_str, ApiKey.revoked.is_(False)),
    )
    if not key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    if key.expires_at and key.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="API key has expired")

    # 更新 last_used_at
    key.last_used_at = datetime.now(UTC)
    await db.flush()

    return key.tenant_id, key.role, key.permissions
