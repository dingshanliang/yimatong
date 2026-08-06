"""试点里程碑 API schema（beads: yimatong-bgag.1）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class MilestoneItem(BaseModel):
    """单个里程碑在时间线中的展示项。"""

    type: str
    label: str
    status: str  # achieved / not_achieved / insufficient_data
    achieved_at: datetime | None = None
    source: str | None = None


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
