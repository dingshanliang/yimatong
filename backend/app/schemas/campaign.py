"""活动与权益 Schema"""

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.campaign import validate_benefit_config_shape, validate_campaign_rules_shape

REQUIRED_RULES_FIELDS = [
    "participation_conditions",
    "claim_limits",
    "validity_period",
    "disclaimer",
    "minor_notice",
    "customer_service_contact",
]


class CampaignCreateRequest(BaseModel):
    name: str
    campaign_type: str
    product_id: uuid.UUID | None = None
    start_at: str
    end_at: str
    rules_json: dict
    description: str | None = None

    @field_validator("rules_json")
    @classmethod
    def validate_rules_json(cls, v: dict) -> dict:
        missing = [f for f in REQUIRED_RULES_FIELDS if f not in v]
        if missing:
            raise ValueError(f"rules_json 缺少必填字段: {', '.join(missing)}")
        return validate_campaign_rules_shape(v)


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    campaign_type: str | None = None
    product_id: uuid.UUID | None = None
    start_at: str | None = None
    end_at: str | None = None
    rules_json: dict | None = None
    description: str | None = None

    @field_validator("rules_json")
    @classmethod
    def validate_rules_json(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        return validate_campaign_rules_shape(v)


class CampaignStatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"draft", "active", "paused", "ended"}
        if v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return v


class BenefitCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    benefit_type: str
    config_json: dict = Field(default_factory=dict)
    stock_total: int = Field(ge=1)
    per_person_limit: int = Field(default=1, ge=1)
    connector_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_benefit(self):
        if self.benefit_type == "cash_red_packet" and self.connector_id is None:
            raise ValueError("connector_id is required for cash_red_packet")
        self.config_json = validate_benefit_config_shape(self.config_json, self.benefit_type)
        return self


class ClaimRequest(BaseModel):
    consumer_id: str
    idempotency_key: str
