"""Analytics Pydantic schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FunnelStep(BaseModel):
    """One result-occurrence metric; heterogeneous units never share a rate."""

    name: str
    value: float
    unit: Literal["count", "yuan"]
    rate: float | None = None


class VisitorCohortSummary(BaseModel):
    window_start: datetime
    window_end: datetime
    attribution_window_hours: Literal[720]
    status: Literal["collecting", "complete"]
    visitors: int
    collecting_visitors: int
    confirmed_order_visitors: int
    confirmed_orders: int
    confirmed_net_amount: float
    confirmed_order_visitor_rate: float | None


class ConversionFunnelResponse(BaseModel):
    """Separate occurrence metrics from an immutable visitor-cohort projection."""

    period_days: int
    window_start: datetime
    window_end: datetime
    report_type: Literal["result_occurrence"]
    conversion_rates_available: Literal[False]
    valid_visits: int
    intent_events: int
    confirmed_claims: int
    confirmed_wecom: int
    order_amount: float
    refund_amount: float
    cancelled_amount: float
    net_amount: float
    order_data_quality: Literal["complete", "incomplete"]
    quarantined_order_count: int
    unattributed_order_count: int
    visitor_cohort: VisitorCohortSummary
    scan_count: int
    claim_count: int
    claim_rate: None
    gmv_amount: float
    gmv_rate: None
    steps: list[FunnelStep]


class AlertItem(BaseModel):
    """单条状态提醒"""

    type: str  # "code_quota" | "campaign_anomaly" | "campaign_status" | "plan_expiry"
    level: str  # "error" | "warning" | "info"
    message: str
    action_url: str


class AlertsResponse(BaseModel):
    """状态提醒响应"""

    alerts: list[AlertItem]


class CampaignRankingItem(BaseModel):
    """活动排行单项"""

    campaign_id: uuid.UUID
    campaign_name: str
    campaign_status: str
    scan_count: int | None
    claim_count: int
    conversion_rate: float | None
    conversion_status: Literal["unavailable"]


class CampaignRankingResponse(BaseModel):
    """活动排行响应"""

    items: list[CampaignRankingItem]
    total: int


class RecentEventItem(BaseModel):
    """最近动态单项"""

    event_type: str  # "scan_surge" | "campaign_status_change" | "channel_anomaly" | "new_signup" | "claim_milestone"
    message: str
    timestamp: datetime
    action_url: str | None = None


class RecentEventsResponse(BaseModel):
    """最近动态响应"""

    events: list[RecentEventItem]
