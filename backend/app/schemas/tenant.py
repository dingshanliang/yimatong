import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    plan: str = "free"
    admin_email: str = Field(..., max_length=255)
    admin_name: str = Field(..., max_length=100)
    admin_password: str = Field(..., min_length=8)


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    quota: dict | None = None
    compliance_settings: dict | None = None
    plan_expires_at: datetime | None = None
    onboarding_progress: dict | None = None


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    plan: str
    plan_expires_at: datetime | None = None
    quota: dict | None = None
    compliance_settings: dict | None = None
    onboarding_progress: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TenantListItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    plan: str
    plan_expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class OpsTaskCreate(BaseModel):
    tenant_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    priority: str = "medium"
    due_date: datetime | None = None


class OpsTaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    status: str | None = None
    priority: str | None = None
    due_date: datetime | None = None


class OpsTaskRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    description: str | None = None
    status: str
    priority: str
    due_date: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
