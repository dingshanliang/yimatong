"""Strict contracts for trusted external order mutations."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

ExternalOrderId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SourceSystem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=50, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
]
CustomerReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[1-9][0-9]{6,14}$")]
Money = Annotated[Decimal, Field(gt=Decimal("0"), max_digits=14, decimal_places=2, allow_inf_nan=False)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)]


class _StrictGmvModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExternalOrderImportItem(_StrictGmvModel):
    external_id: ExternalOrderId
    amount: Money
    currency: Literal["CNY"] = "CNY"
    status: Literal["confirmed"] = "confirmed"
    order_time: AwareDatetime
    phone: PhoneNumber | None = None
    customer_reference: CustomerReference | None = None
    product_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    channel: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None

    @model_validator(mode="after")
    def require_matching_identity(self):
        if self.phone is None and self.customer_reference is None:
            raise ValueError("phone or customer_reference is required")
        return self


class ExternalOrderImportRequest(_StrictGmvModel):
    source_system: SourceSystem
    orders: list[ExternalOrderImportItem] = Field(min_length=1, max_length=200)


class ExternalOrderRefundRequest(_StrictGmvModel):
    source_system: SourceSystem
    amount: Money
    currency: Literal["CNY"] = "CNY"
    reason: Reason
    occurred_at: AwareDatetime


class ExternalOrderCancelRequest(_StrictGmvModel):
    source_system: SourceSystem
    reason: Reason
    occurred_at: AwareDatetime


class AutoAttributionRequest(_StrictGmvModel):
    window_hours: Literal[168, 720] = 720
    limit: int = Field(default=200, ge=1, le=500)


class ConfirmedAttributionRequest(_StrictGmvModel):
    external_order_id: uuid.UUID
    consumer_id: uuid.UUID
    scan_event_id: uuid.UUID
    scan_time: AwareDatetime
    attribution_window_hours: Literal[168, 720] = 720

    def canonical_payload(self) -> dict[str, uuid.UUID | datetime | int]:
        return {
            "external_order_id": self.external_order_id,
            "consumer_id": self.consumer_id,
            "scan_event_id": self.scan_event_id,
            "scan_time": self.scan_time,
            "attribution_window_hours": self.attribution_window_hours,
        }


class GmvDailyOccurrence(_StrictGmvModel):
    date: str
    gmv: float
    orders: int


class GmvChannelOccurrence(_StrictGmvModel):
    channel: str
    gmv: float
    orders: int


class GmvCampaignOccurrence(_StrictGmvModel):
    campaign_id: uuid.UUID
    campaign_name: str
    gmv: float
    orders: int
    avg_confidence: float


class GmvDashboardResponse(_StrictGmvModel):
    """Confirmed occurrence facts; no cross-population attribution rate."""

    total_gmv: float
    attributed_orders: int
    total_orders: int
    unattributed_orders: int
    quarantined_order_count: int
    order_data_quality: Literal["complete", "incomplete"]
    attribution_rate: None
    attribution_rate_status: Literal["unavailable_non_cohort"]
    daily_trend: list[GmvDailyOccurrence]
    by_channel: list[GmvChannelOccurrence]
    by_campaign: list[GmvCampaignOccurrence]


class GmvRoiItem(_StrictGmvModel):
    """Campaign occurrence value projection without an eligible visitor cohort."""

    campaign_id: uuid.UUID
    campaign_name: str
    status: str
    budget: float | None
    attributed_gmv: float
    attributed_orders: int
    scan_count: None
    scan_uv: None
    scan_cost: None
    conversion_rate: None
    conversion_rate_status: Literal["unavailable_missing_campaign_eligible_cohort"]
    roi: float
    avg_confidence: float
