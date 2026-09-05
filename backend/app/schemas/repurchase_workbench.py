import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkCategory = Literal[
    "coupon_issue_failure",
    "coupon_expiry_unreached",
    "notification_failure",
    "payment_redemption_conflict",
    "refund_unsynced",
    "membership_mapping_conflict",
    "unattributed_order",
    "source_coverage",
]


class RepurchaseWorkItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: WorkCategory
    business_ref: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    impact_summary: str = Field(min_length=1, max_length=500)
    priority: Literal["urgent", "high", "normal"]
    owner_account_id: uuid.UUID
    due_at: datetime
    reason: str = Field(min_length=1, max_length=1000)
    evidence: dict = Field(default_factory=dict)


class RepurchaseWorkItemTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["in_progress", "waiting_external", "resolved", "no_action"]
    reason: str = Field(min_length=1, max_length=1000)
    conclusion: str | None = Field(default=None, max_length=1000)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_terminal_conclusion(self):
        if self.status in {"resolved", "no_action"} and not self.conclusion:
            raise ValueError("terminal work item status requires conclusion")
        return self


class RepurchaseWorkItemAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner_account_id: uuid.UUID
    due_at: datetime
    reason: str = Field(min_length=1, max_length=1000)
    evidence: dict = Field(default_factory=dict)


class RepurchaseWorkItemAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["resync", "correction"]
    reason: str = Field(min_length=1, max_length=1000)
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_correction_snapshots(self):
        if self.action == "correction" and (not self.before or not self.after):
            raise ValueError("controlled correction requires before and after snapshots")
        return self
