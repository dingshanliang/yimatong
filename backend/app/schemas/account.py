import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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


class AccountRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str | None = None
    email: str
    name: str
    initial_password: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AccountUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    role_ids: list[uuid.UUID] | None = None
