"""客户上线门禁 API schema。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LaunchReleaseCreateRequest(BaseModel):
    page_version_id: uuid.UUID
    campaign_id: uuid.UUID
    code_batch_id: uuid.UUID


class LaunchActionRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1, max_length=100)


class LaunchReleaseActionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    page_template_id: uuid.UUID
    page_version_id: uuid.UUID
    campaign_id: uuid.UUID
    code_batch_id: uuid.UUID
    status: str
    ready: bool
    readiness_snapshot: dict
    content_digest: str
    brand_confirmed_by: uuid.UUID | None = None
    brand_confirmed_at: datetime | None = None
    launched_by: uuid.UUID | None = None
    launched_at: datetime | None = None
    failure_reason: str | None = None
    suspension_reason: str | None = None
