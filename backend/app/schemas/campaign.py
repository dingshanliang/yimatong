"""活动与权益 Schema"""

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.campaign_validation import validate_benefit_config_shape, validate_campaign_rules_shape

REQUIRED_RULES_FIELDS = [
    "participation_conditions",
    "claim_limits",
    "validity_period",
    "disclaimer",
    "minor_notice",
    "customer_service_contact",
]


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: str = Field(min_length=1, max_length=50)
    product_id: uuid.UUID | None = None
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    rules_json: dict
    description: str | None = Field(default=None, max_length=500)

    @field_validator("rules_json")
    @classmethod
    def validate_rules_json(cls, v: dict) -> dict:
        missing = [f for f in REQUIRED_RULES_FIELDS if f not in v]
        if missing:
            raise ValueError(f"rules_json 缺少必填字段: {', '.join(missing)}")
        return validate_campaign_rules_shape(v)


class CampaignUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    campaign_type: str | None = Field(default=None, min_length=1, max_length=50)
    product_id: uuid.UUID | None = None
    start_at: str | None = Field(default=None, min_length=1)
    end_at: str | None = Field(default=None, min_length=1)
    rules_json: dict | None = None
    description: str | None = Field(default=None, max_length=500)

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


class BenefitUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    benefit_type: str | None = Field(default=None, min_length=1, max_length=50)
    config_json: dict | None = None
    stock_total: int | None = Field(default=None, ge=0)
    per_person_limit: int | None = Field(default=None, ge=1)
    connector_id: uuid.UUID | None = None
    status: str | None = None

    @model_validator(mode="after")
    def validate_benefit(self):
        if self.benefit_type and self.config_json:
            self.config_json = validate_benefit_config_shape(self.config_json, self.benefit_type)
        return self


class ClaimRequest(BaseModel):
    consumer_id: str
    idempotency_key: str
