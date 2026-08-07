"""试点复盘 API schema（beads: yimatong-bgag.2 / bgag.8）。"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


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
    content: str
    owner_id: uuid.UUID | None = None
    due_date: date | None = None
    status: str = "pending"
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

    goal: str | None = None
    issues: str | None = None
    actions: list[ActionItem] | None = None
    next_review_date: date | None = None
    supplementary_notes: str | None = None
    mark_completed: bool = False
