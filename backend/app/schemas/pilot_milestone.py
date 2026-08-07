"""试点里程碑 API schema（beads: yimatong-bgag.1 / bgag.7）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.constants.pilot import PilotMilestoneType


class MilestoneCorrectionItem(BaseModel):
    """里程碑更正记录（PRD §6.2：追加式，不改写原始事实）。"""

    corrected_at: datetime
    source: str
    reason: str
    corrected_by: uuid.UUID | None = None
    created_at: datetime


class MilestoneItem(BaseModel):
    """单个里程碑在时间线中的展示项。"""

    type: str
    label: str
    status: str  # achieved / not_achieved / insufficient_data
    achieved_at: datetime | None = None
    source: str | None = None
    # 更正存在时保留原始事实以供审计（PRD §6.2：原始记录只增不改）
    original_achieved_at: datetime | None = None
    original_source: str | None = None
    corrections: list[MilestoneCorrectionItem] = []


class DerivedDuration(BaseModel):
    """两个里程碑之间的派生时长（秒）。缺失源数据时为 None。"""

    label: str
    from_type: str
    to_type: str
    seconds: float | None = None
    status: str  # computed / insufficient_data


class MilestoneTimelineResponse(BaseModel):
    """租户试点里程碑时间线。"""

    tenant_id: uuid.UUID
    milestones: list[MilestoneItem]
    derived_durations: list[DerivedDuration]


class MilestoneCorrectionRequest(BaseModel):
    """里程碑更正请求（PRD §6.2：追加式，必填 reason）。仅平台管理员。"""

    milestone_type: PilotMilestoneType
    corrected_at: datetime = Field(..., description="更正后的达成时间")
    source: str = Field(..., description="更正后的事实来源说明")
    reason: str = Field(..., min_length=1, description="更正原因（必填）")
