"""邀请码相关 Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.utils.email import normalize_email


class InviteCodeCreateRequest(BaseModel):
    tenant_type: str = Field(default="brand", description="租户类型")
    max_uses: int = Field(default=1, ge=1, le=1000, description="最大使用次数")
    expires_in_days: int | None = Field(default=30, ge=1, le=365, description="有效期（天）")


class InviteCodeResponse(BaseModel):
    id: uuid.UUID
    code: str
    tenant_type: str
    max_uses: int
    used_count: int
    status: str
    expires_at: datetime | None
    created_at: datetime
    registration_url: str = Field(..., description="基于 canonical Admin 公网基址生成的完整注册链接")

    model_config = {"from_attributes": True}


class TenantRegisterRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=50, description="邀请码")
    name: str = Field(..., min_length=1, max_length=100, description="租户名称")
    slug: str | None = Field(None, max_length=50, description="URL 标识")
    admin_email: EmailStr
    admin_name: str = Field(..., min_length=1, max_length=100)
    admin_password: str = Field(..., min_length=8, max_length=128)
    industry: str | None = Field(None, max_length=50)

    @field_validator("admin_email")
    @classmethod
    def _normalize_admin_email(cls, value: EmailStr) -> str:
        return normalize_email(str(value))


class TenantRegisterResponse(BaseModel):
    tenant_id: uuid.UUID
    tenant_slug: str
    message: str
