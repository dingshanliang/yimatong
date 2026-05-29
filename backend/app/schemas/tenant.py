import uuid

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


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    plan: str
    quota: dict | None = None
    compliance_settings: dict | None = None

    model_config = {"from_attributes": True}
