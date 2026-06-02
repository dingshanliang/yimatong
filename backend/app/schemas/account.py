import uuid

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

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


class AccountUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    role_ids: list[uuid.UUID] | None = None
