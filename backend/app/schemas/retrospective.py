"""试点复盘 API schema（beads: yimatong-bgag.2）。"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class ScorecardMetric(BaseModel):
    value: float | int | None = None
    status: str
    unit: str | None = None


class ActionItem(BaseModel):
    content: str
    owner_id: uuid.UUID | None = None
    due_date: date | None = None
    status: str = "pending"


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
