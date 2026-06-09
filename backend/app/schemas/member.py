"""会员与积分相关 Pydantic schemas"""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = Field(None, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v


class AwardPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class SpendPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class PointRuleCreate(BaseModel):
    rule_type: Literal["scan", "first_scan", "register", "checkin", "repurchase", "activity"]
    points: int = Field(gt=0)
    daily_limit: int = Field(ge=0, default=0)
    description: str | None = None
    config: dict | None = None


class PointRuleUpdate(BaseModel):
    points: int | None = None
    daily_limit: int | None = None
    description: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class PointProductCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    image_url: str | None = None
    points_cost: int = Field(gt=0)
    stock: int = Field(ge=0, default=0)
    benefit_id: uuid.UUID | None = None
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int = Field(ge=0, default=1)
    sort_order: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class PointProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    points_cost: int | None = None
    stock: int | None = None
    benefit_id: uuid.UUID | None = None
    enabled: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int | None = None
    sort_order: int | None = None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class ExchangeRequest(BaseModel):
    consumer_id: uuid.UUID
    product_id: uuid.UUID


class LeadCaptureRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    region: str | None = None
    intention: str | None = None
    public_id: str


# Alias for consumer-facing endpoint compatibility
PointsExchangeRequest = ExchangeRequest
