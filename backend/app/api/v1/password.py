import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_role, get_current_tenant, get_redis_cache
from app.models.tenant import Account
from app.services.audit import write_audit_log
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.security import clear_auth_cookies, hash_password, validate_password_strength, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

ADMIN_ROLES = {"admin", "platform_admin"}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    account_id: uuid.UUID
    new_password: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=2, max_length=200)


def _validate_password(password: str) -> None:
    """Wrapper that converts ValueError to HTTPException for API layer."""
    try:
        validate_password_strength(password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not verify_password(body.old_password, account.hashed_password):
        raise HTTPException(status_code=401, detail="Old password is incorrect")
    _validate_password(body.new_password)
    account.hashed_password = hash_password(body.new_password)
    account.must_change_password = False
    account.auth_version += 1
    await db.commit()
    response = JSONResponse(content={"detail": "Password changed"})
    clear_auth_cookies(response)
    return response


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    role: str = Depends(get_current_role),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    if role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="仅管理员可重置密码")
    # 速率限制：每 account_id 每分钟最多 10 次。必须跨 worker 共享，否则每个
    # worker 各自计 10 次/分钟，与 login/confirm-reset 的认证边界限流不一致。
    try:
        allowed, _ = await cache.rate_limit_check_shared(
            f"admin_reset:{body.account_id}", max_attempts=10, window_seconds=60
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="重置服务暂时不可用，请稍后重试") from exc
    if not allowed:
        raise HTTPException(status_code=429, detail="重置操作过于频繁", headers={"Retry-After": "60"})
    result = await db.execute(select(Account).where(Account.id == body.account_id, Account.tenant_id == tenant_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _validate_password(body.new_password)
    try:
        account.hashed_password = hash_password(body.new_password)
        account.auth_version += 1
        await write_audit_log(
            db,
            operator_id=str(actor_id),
            target_tenant_id=str(tenant_id),
            action="account_password_reset_by_admin",
            resource=f"account:{account.id}",
            details={"reason": body.reason},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"detail": "Password reset"}
