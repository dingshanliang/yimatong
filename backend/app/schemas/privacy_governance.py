import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConsumerPrivacyRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_type: Literal["access", "copy", "correct", "delete", "restrict", "withdraw_consent", "close_membership"]
    reason: str = Field(min_length=2, max_length=1000)
    evidence: dict = Field(default_factory=dict)


class PrivacyRequestAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["assign", "restrict", "complete", "reject"]
    reason: str = Field(min_length=2, max_length=1000)
    owner_account_id: uuid.UUID | None = None
    outcome: str | None = Field(default=None, max_length=1000)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_action_details(self):
        if self.action == "assign" and self.owner_account_id is None:
            raise ValueError("assignment requires owner_account_id")
        if self.action in {"complete", "reject"} and not self.outcome:
            raise ValueError("terminal privacy request action requires outcome")
        return self


class PiiRevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=1000)
    ticket_ref: str | None = Field(default=None, max_length=160)


class SensitiveExportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_status: Literal["active"] = "active"
    membership_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    consumer_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    joined_from: datetime | None = None
    joined_until: datetime | None = None

    @model_validator(mode="after")
    def validate_joined_window(self):
        if self.joined_from and self.joined_until and self.joined_until <= self.joined_from:
            raise ValueError("sensitive export joined window must increase")
        return self


class SensitiveExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=1000)
    recipient_purpose: str = Field(min_length=2, max_length=500)
    requested_fields: list[Literal["membership_number", "nickname", "phone"]] = Field(min_length=1, max_length=3)
    filters: SensitiveExportFilters = Field(default_factory=SensitiveExportFilters)

    @model_validator(mode="after")
    def require_full_pii_scope(self):
        if "phone" not in self.requested_fields:
            raise ValueError("sensitive export must explicitly include a sensitive field")
        return self


class SensitiveExportAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=1000)


class SensitiveExportDownload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    download_token: str = Field(min_length=32, max_length=256)
    reason: str = Field(min_length=2, max_length=1000)
