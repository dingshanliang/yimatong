"""试点复盘 API schema（beads: yimatong-bgag.2 / bgag.8）。"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScorecardMetric(BaseModel):
    value: float | int | None = None
    status: str
    unit: str | None = None


# 动作承接处置（PRD §8：上期动作未完成 → 新一期必须显式选择继续/调整/放弃）
ACTION_DISPOSITION_CONTINUE = "continue"
ACTION_DISPOSITION_ADJUST = "adjust"
ACTION_DISPOSITION_ABANDON = "abandon"
ACTION_DISPOSITIONS = {ACTION_DISPOSITION_CONTINUE, ACTION_DISPOSITION_ADJUST, ACTION_DISPOSITION_ABANDON}
ActionDisposition = Literal["continue", "adjust", "abandon"]


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=1000)
    owner_id: uuid.UUID | None = None
    due_date: date | None = None
    status: Literal["pending", "completed"] = "pending"
    # PRD §8：上期动作承接。carryover=True 表示从上一期复盘顺延而来；
    # carryover_disposition 必须在完成本期复盘前显式填 continue/adjust/abandon。
    carryover: bool = False
    carryover_disposition: ActionDisposition | None = None


class RetrospectiveRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    period_day: int
    window_start: datetime
    window_end: datetime
    next_review_date: date
    status: str  # 存储态：pending/completed
    version: int = Field(ge=1)
    derived_status: str  # 派生态：含 overdue/overdue_completed
    goal: str | None = None
    scorecard_snapshot: dict
    issues: str | None = None
    actions: list[dict]
    completed_at: datetime | None = None
    completed_by: uuid.UUID | None = None
    supplementary_notes: str | None = None
    ops_task_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class RetrospectiveUpdateRequest(BaseModel):
    """复盘填写/完成请求。

    首次填写可更新 goal/issues/actions/next_review_date；一旦 status=completed，
    仅 supplementary_notes 可追加（PRD §6.1 快照冻结）。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal: str | None = Field(default=None, max_length=5000)
    issues: str | None = Field(default=None, max_length=10000)
    actions: list[ActionItem] | None = Field(default=None, max_length=100)
    next_review_date: date | None = None
    supplementary_notes: str | None = Field(default=None, min_length=1, max_length=5000)
    mark_completed: bool = False

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个复盘变更字段")
        return self
