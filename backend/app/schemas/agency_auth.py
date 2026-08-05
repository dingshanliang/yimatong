"""Agency 授权相关 Schema"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class AuthorizationCreate(BaseModel):
    agency_tenant_id: uuid.UUID | None = None
    agency_slug: str | None = Field(None, min_length=1, max_length=50)
    scope: list[str] = Field(default_factory=lambda: ["products", "pages", "campaigns", "codes", "analytics"])

    @model_validator(mode="after")
    def _exactly_one_agency_reference(self):
        if (self.agency_tenant_id is None) == (self.agency_slug is None):
            raise ValueError("请使用代运营服务商标识或 ID 指定一个服务商")
        if self.agency_slug is not None:
            self.agency_slug = self.agency_slug.strip().lower()
        return self


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
