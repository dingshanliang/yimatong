"""Strict API contracts for tenant risk-rule operations."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RiskRuleType = Literal[
    "ip_frequency",
    "phone_frequency",
    "device_frequency",
    "scan_frequency",
    "cross_region",
]
RiskAction = Literal["block", "warn"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FrequencyConfig(_StrictModel):
    window_minutes: int = Field(ge=1, le=1440)
    max_requests: int = Field(ge=1, le=1_000_000)


class TimeWindowConfig(_StrictModel):
    allowed_hours: list[Annotated[int, Field(ge=0, le=23)]] = Field(min_length=1, max_length=24)


class RegionRestrictionConfig(_StrictModel):
    allowed_regions: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(min_length=1, max_length=100)


class CrossRegionConfig(_StrictModel):
    """Cross-region evaluation is the presence of the exact scan observation."""


class BudgetLimitConfig(_StrictModel):
    max_budget: float = Field(gt=0, le=1_000_000_000)


class StockLimitConfig(_StrictModel):
    max_stock: int = Field(gt=0, le=1_000_000_000)


RiskConfig = (
    FrequencyConfig
    | TimeWindowConfig
    | RegionRestrictionConfig
    | CrossRegionConfig
    | BudgetLimitConfig
    | StockLimitConfig
)


_CONFIG_MODEL = {
    "ip_frequency": FrequencyConfig,
    "phone_frequency": FrequencyConfig,
    "device_frequency": FrequencyConfig,
    "scan_frequency": FrequencyConfig,
    "time_window": TimeWindowConfig,
    "region_restriction": RegionRestrictionConfig,
    "cross_region": CrossRegionConfig,
    "budget_limit": BudgetLimitConfig,
    "stock_limit": StockLimitConfig,
}


class RiskRuleCreate(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    rule_type: RiskRuleType
    action: RiskAction
    config: dict

    @model_validator(mode="after")
    def validate_config(self) -> RiskRuleCreate:
        self.config = _CONFIG_MODEL[self.rule_type].model_validate(self.config).model_dump()
        return self


class RiskRuleUpdate(_StrictModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    action: RiskAction | None = None
    config: dict | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> RiskRuleUpdate:
        if all(value is None for value in (self.name, self.action, self.config, self.enabled)):
            raise ValueError("At least one rule field must change")
        return self


class ExactScanRiskEvaluate(_StrictModel):
    scan_event_id: uuid.UUID
    rule_id: uuid.UUID


class RiskPauseResume(_StrictModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
