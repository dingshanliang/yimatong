import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.utils.security import validate_password_strength


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None


class OrganizationRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    account_count: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class OrganizationUpdate(BaseModel):
    """PATCH schema — 只传需要改的字段。parent_id 传 null 表示清除上级，不传则不改动。"""

    name: str | None = Field(None, min_length=1, max_length=100)
    parent_id: uuid.UUID | None = Field(None, description="上级组织 ID，传 null 清除上级")


class AccountCreate(BaseModel):
    email: str = Field(..., max_length=255)
    name: str = Field(..., max_length=100)
    password: str | None = Field(None, min_length=8)
    organization_id: uuid.UUID
    role_ids: list[uuid.UUID] = []

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                validate_password_strength(v)
            except ValueError as e:
                raise ValueError(f"密码不符合要求: {e}") from e
        return v


class AccountRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str | None = None
    email: str
    name: str
    is_active: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AccountCreateResponse(AccountRead):
    """创建账户专用响应 — 包含一次性初始密码。"""

    initial_password: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    organization_id: uuid.UUID | None = Field(None, description="转移至新组织")
    role_ids: list[uuid.UUID] | None = None


class AccountStatusUpdate(BaseModel):
    is_active: bool
    reason: str = Field(..., min_length=2, max_length=200)

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("操作原因至少需要 2 个字符")
        return normalized
