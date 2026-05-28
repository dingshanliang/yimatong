import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.email == body.email))
    account = result.scalar_one_or_none()

    now_naive = utcnow()

    if not account or not verify_password(body.password, account.hashed_password):
        if account:
            account.failed_login_attempts += 1
            if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                account.locked_until = now_naive + timedelta(minutes=LOCK_DURATION_MINUTES)
            await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if account.locked_until and account.locked_until > now_naive:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now_naive
    await db.commit()

    access = create_access_token(str(account.tenant_id), str(account.id), "admin")
    refresh = create_refresh_token(str(account.id))
    return TokenResponse(access_token=access, refresh_token=refresh)


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
    return TokenResponse(access_token=access, refresh_token=refresh)
