"""Bounded public consent and lead request contracts."""

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConsentGrantRequest(_StrictRequest):
    purpose: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=50)
    policy_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        if _PURPOSE_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid purpose")
        return value

    @field_validator("policy_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid policy digest")
        return value


class ConsentWithdrawRequest(_StrictRequest):
    idempotency_key: str = Field(min_length=8, max_length=100)


class LeadCaptureRequest(_StrictRequest):
    consent_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=100)
    name: str | None = Field(default=None, max_length=100)
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    region: str | None = Field(default=None, max_length=100)
    intention: str | None = Field(default=None, max_length=500)

    @field_validator("name", "region", "intention")
    @classmethod
    def reject_empty_strings(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("must not be empty")
        return value
