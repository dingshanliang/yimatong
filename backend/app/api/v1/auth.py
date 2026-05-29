import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_account_id
from app.models.tenant import Account
from app.utils import utcnow
from app.utils.security import create_access_token, create_refresh_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.email == body.email))
    account = result.scalar_one_or_none()

    now = utcnow()

    if not account or not verify_password(body.password, account.hashed_password):
        if account:
            account.failed_login_attempts += 1
            if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                account.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
            await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if account.locked_until and account.locked_until > now:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    await db.commit()

    access = create_access_token(str(account.tenant_id), str(account.id), "admin")
    refresh = create_refresh_token(str(account.id))
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from app.utils.security import verify_refresh_token

    payload = verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    account_id = payload["sub"]
    result = await db.execute(select(Account).where(Account.id == uuid.UUID(account_id)))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=401, detail="Account not found")
    access = create_access_token(str(account.tenant_id), str(account.id), "admin")
    refresh = create_refresh_token(str(account.id))
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


class MeResponse(BaseModel):
    id: str
    email: str
    name: str
    tenant_id: str
    organization_id: str | None = None
    role: str


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return MeResponse(
        id=str(account.id),
        email=account.email,
        name=account.name,
        tenant_id=str(account.tenant_id),
        organization_id=str(account.organization_id) if account.organization_id else None,
        role=request.state.role if hasattr(request.state, "role") else "admin",
    )


@router.post("/logout")
async def logout():
    """登出端点（客户端清除 token 即可，服务端无状态）"""
    return {"status": "ok"}
