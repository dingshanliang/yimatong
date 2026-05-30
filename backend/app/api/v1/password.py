import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.tenant import Account
from app.utils.security import hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
    if not re.search(r"[a-zA-Z]", password):
        raise HTTPException(status_code=422, detail="密码必须包含字母")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=422, detail="密码必须包含数字")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    account_id: uuid.UUID
    new_password: str = Field(..., min_length=1)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not verify_password(body.old_password, account.hashed_password):
        raise HTTPException(status_code=401, detail="Old password is incorrect")
    validate_password_strength(body.new_password)
    account.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"detail": "Password changed"}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(select(Account).where(Account.id == body.account_id, Account.tenant_id == tenant_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    validate_password_strength(body.new_password)
    account.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"detail": "Password reset"}
