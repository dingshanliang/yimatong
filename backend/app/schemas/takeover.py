"""既有码接管 API 的输入输出契约。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class TakeoverProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source_system: str = Field(..., min_length=1, max_length=100)
    mode: Literal["legacy_redirect", "cname"]
    source_domain: str | None = Field(default=None, max_length=253)
    consumer_domain: str | None = Field(default=None, max_length=253)
    sample_url: HttpUrl
    url_rule: dict = Field(default_factory=lambda: {"kind": "path_tail"})
    code_scope: dict = Field(default_factory=dict)
    control_facts: dict = Field(default_factory=dict)
    responsible_person: str = Field(..., min_length=1, max_length=100)
    technical_owner: str | None = Field(default=None, max_length=100)
    rollback_contact: str = Field(..., min_length=1, max_length=100)
    fallback_url: HttpUrl

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "TakeoverProjectCreate":
        if self.mode == "cname" and not self.consumer_domain:
            raise ValueError("CNAME 接管必须配置消费者扫码域名")
        if self.mode == "legacy_redirect" and not self.source_domain:
            raise ValueError("旧系统跳转必须配置旧系统域名")
        if self.url_rule.get("kind") not in {"path_tail", "query", "fixed"}:
            raise ValueError("只支持路径尾段或指定 query 参数取码")
        if self.url_rule.get("kind") == "query" and not self.url_rule.get("key"):
            raise ValueError("query 取码规则必须指定参数名")
        return self


class TakeoverProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    consumer_domain: str | None = Field(default=None, max_length=253)
    code_scope: dict | None = None
    control_facts: dict | None = None
    responsible_person: str | None = Field(default=None, min_length=1, max_length=100)
    technical_owner: str | None = Field(default=None, max_length=100)
    rollback_contact: str | None = Field(default=None, min_length=1, max_length=100)
    fallback_url: HttpUrl | None = None


class TakeoverRouteCreate(BaseModel):
    source_url: HttpUrl
    target_url: HttpUrl
    sample_codes: list[str] = Field(default_factory=list, max_length=1000)
    code_prefix: str | None = Field(default=None, max_length=100)
    extraction_rule: dict | None = None


class ExternalExecutionRequest(BaseModel):
    execution_reference: str = Field(..., min_length=1, max_length=200)
    executed_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)


class ObservationRequest(BaseModel):
    checked_url: HttpUrl
    success_rate: float = Field(..., ge=0, le=1)
    error_rate: float = Field(..., ge=0, le=1)
    latency_ms: float | None = Field(default=None, ge=0)
    h5_reach_rate: float = Field(default=0, ge=0, le=1)
    target_match: bool = False
    metrics: dict = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=100)


class TakeoverResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    source_system: str
    mode: str
    source_domain: str | None = None
    consumer_domain: str | None = None
    expected_cname: str
    sample_url: str
    url_rule: dict
    code_scope: dict
    control_facts: dict
    responsible_person: str
    technical_owner: str | None = None
    rollback_contact: str
    fallback_url: str
    status: str
    assessment: dict
    readiness_snapshot: dict
    readiness_digest: str | None = None
    configuration_version: int
    brand_confirmed_by: uuid.UUID | None = None
    brand_confirmed_at: datetime | None = None
    brand_confirmation_digest: str | None = None
    created_at: datetime
    updated_at: datetime


class TakeoverImportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    file_name: str
    file_sha256: str
    counts: dict
    errors: list[dict] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class TakeoverRouteResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    mode: str
    domain: str | None
    source_url: str
    target_url: str
    extraction_rule: dict
    sample_codes: list[str]
    code_prefix: str | None
    status: str
    content_digest: str
    readiness_snapshot: dict
    executed_at: datetime | None = None
