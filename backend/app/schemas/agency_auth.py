"""Agency 授权相关 Schema"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AuthorizationCreate(BaseModel):
    agency_tenant_id: uuid.UUID
    scope: list[str] = Field(default_factory=lambda: ["pages", "campaigns", "analytics"])


class AuthorizationUpdate(BaseModel):
    scope: list[str] | None = None
    status: str | None = None


class AuthorizationResponse(BaseModel):
    id: uuid.UUID
    agency_tenant_id: uuid.UUID
    client_tenant_id: uuid.UUID
    agency_name: str | None = None
    client_name: str | None = None
    client_slug: str | None = None
    scope: list[str]
    status: str
    granted_by: uuid.UUID | None = None
    granted_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


class AuthorizationListResponse(BaseModel):
    items: list[AuthorizationResponse]
    total: int
