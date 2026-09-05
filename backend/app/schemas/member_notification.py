"""Member message-center API contracts."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MarketingSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketing_consent_id: uuid.UUID
    template_code: str = Field(min_length=1, max_length=80)


class ServiceChannelGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_code: str = Field(min_length=1, max_length=80)


class ServiceChannelPreferenceRequest(BaseModel):
    enabled: bool


class MemberNotificationItem(BaseModel):
    id: uuid.UUID
    notification_class: str
    notification_type: str
    title: str
    body: str
    action_path: str | None
    facts: dict
    occurred_at: datetime
    read_at: datetime | None


class MemberNotificationPreferenceItem(BaseModel):
    marketing_enabled: bool
    service_wechat_enabled: bool
    critical_sms_enabled: bool
    marketing_opted_in_at: datetime | None
    marketing_opted_out_at: datetime | None


class DeliveryAttemptRequest(BaseModel):
    result: str = Field(pattern="^(accepted|transient_failure|permanent_failure)$")
    channel_message_ref: str | None = Field(default=None, max_length=160)
    error_code: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_result_detail(self):
        if self.result == "accepted" and not self.channel_message_ref:
            raise ValueError("accepted_delivery_requires_channel_reference")
        if self.result != "accepted" and not self.error_code:
            raise ValueError("failed_delivery_requires_error_code")
        return self


class MarketingNotificationCreate(BaseModel):
    membership_id: uuid.UUID
    notification_type: str = Field(pattern="^(coupon_expiry|repurchase_invite|gift_coupon)$")
    source_product: str = Field(min_length=1, max_length=30)
    source_event_id: str = Field(min_length=1, max_length=160)
    source_event_version: int = Field(ge=1)
    object_ref: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=500)
    action_path: str | None = Field(default=None, max_length=300)
    facts: dict = Field(default_factory=dict)
    occurred_at: datetime
    template_code: str = Field(min_length=1, max_length=80)
    template_version: str = Field(min_length=1, max_length=40)
