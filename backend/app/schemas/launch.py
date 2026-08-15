"""客户上线门禁 API schema。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LaunchReleaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    page_version_id: uuid.UUID
    campaign_id: uuid.UUID
    code_batch_id: uuid.UUID
    idempotency_key: str = Field(..., min_length=1, max_length=100)


class LaunchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str = Field(..., min_length=1, max_length=100)


class LaunchSuspendRequest(LaunchActionRequest):
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


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
    readiness_sample_code: dict | None = None
    content_digest: str
    brand_confirmed_by: uuid.UUID | None = None
    brand_confirmed_at: datetime | None = None
    launched_by: uuid.UUID | None = None
    launched_at: datetime | None = None
    failure_reason: str | None = None
    suspension_reason: str | None = None
