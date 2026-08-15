"""H5 权益领取 Schema"""

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _BenefitClaimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scan_token: str | None = Field(default=None, min_length=1, max_length=4096)
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v


class BenefitClaimRequest(_BenefitClaimPayload):
    benefit_id: uuid.UUID


class PublicBenefitClaimRequest(_BenefitClaimPayload):
    benefit_id: uuid.UUID | None = None
