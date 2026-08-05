import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=255)
    permission_ids: list[uuid.UUID] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=255)
    permission_ids: list[uuid.UUID] | None = None


class PermissionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=255)


class PermissionRead(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None = None

    model_config = {"from_attributes": True}


class RoleRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    permissions: list[PermissionRead] = []
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
