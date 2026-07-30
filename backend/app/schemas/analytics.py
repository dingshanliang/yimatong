"""Analytics Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class FunnelStep(BaseModel):
    """转化漏斗单步"""

    name: str
    value: int
    rate: float  # 相对扫码量的百分比


class ConversionFunnelResponse(BaseModel):
    """转化漏斗响应"""

    period_days: int
    scan_count: int
    claim_count: int
    claim_rate: float
    signup_count: int
    signup_rate: float
    private_domain_count: int
    private_domain_rate: float
    gmv_amount: float
    gmv_rate: float
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
    scan_count: int
    claim_count: int
    conversion_rate: float


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
