"""Validated request contracts for sensitive data exports."""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExportReasonRequest(BaseModel):
    """Every prepared export must carry a human-provided audit reason."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class AnalyticsExportRequest(ExportReasonRequest):
    export_type: Literal[
        "scan_events",
        "scan_stats",
        "campaign_dashboard",
        "risk_dashboard",
        "regional_dashboard",
    ]
    format: Literal["xlsx"] = "xlsx"
    start_date: date | None = None
    end_date: date | None = None
    campaign_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    days_back: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def validate_scope(self) -> "AnalyticsExportRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.export_type == "regional_dashboard":
            if self.org_id is None or self.days_back is None:
                raise ValueError("regional_dashboard requires org_id and days_back")
        elif self.org_id is not None or self.days_back is not None:
            raise ValueError("org_id and days_back are only valid for regional_dashboard")
        if self.export_type != "campaign_dashboard" and self.campaign_id is not None:
            raise ValueError("campaign_id is only valid for campaign_dashboard")
        return self


class RiskExportRequest(ExportReasonRequest):
    data_type: Literal["alerts", "diversions"] = "alerts"


class CodeBatchExportRequest(ExportReasonRequest):
    pass


class TakeoverErrorExportRequest(ExportReasonRequest):
    pass
