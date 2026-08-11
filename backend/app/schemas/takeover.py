"""既有码接管 API 的输入输出契约。"""

import ipaddress
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def validate_takeover_domain_name(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise ValueError("domain must be a hostname, not an IP address")
    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid domain name") from exc
    labels = ascii_domain.split(".")
    if (
        len(labels) < 2
        or len(ascii_domain) > 253
        or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels)
        or any(not all(character.isalnum() or character == "-" for character in label) for label in labels)
    ):
        raise ValueError("invalid domain name")
    return ascii_domain


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

    @field_validator("source_domain", "consumer_domain")
    @classmethod
    def validate_domains(cls, value: str | None) -> str | None:
        return validate_takeover_domain_name(value)

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

    @field_validator("consumer_domain")
    @classmethod
    def validate_consumer_domain(cls, value: str | None) -> str | None:
        return validate_takeover_domain_name(value)


class TakeoverRouteCreate(BaseModel):
    source_url: HttpUrl
    target_url: HttpUrl
    sample_codes: list[str] = Field(default_factory=list, max_length=1000)
    code_prefix: str | None = Field(default=None, max_length=100)
    extraction_rule: dict | None = None


class ExternalExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(..., min_length=1, max_length=100)
    execution_reference: str = Field(..., min_length=1, max_length=200)

    @field_validator("idempotency_key", "execution_reference")
    @classmethod
    def validate_nonblank_external_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_url: HttpUrl


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=500)
    idempotency_key: str = Field(..., min_length=1, max_length=80)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def validate_nonblank_rollback_input(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()


class TakeoverResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    source_system: str
    mode: str
    source_domain: str | None = None
    consumer_domain: str | None = None
    expected_cname: str
    domain_verification_record_name: str | None = None
    domain_verification_record_value: str
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
