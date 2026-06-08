"""H5 权益领取 Schema"""

import re
import uuid

from pydantic import BaseModel, field_validator


class BenefitClaimRequest(BaseModel):
    benefit_id: uuid.UUID
    scan_token: str | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v
