"""试点里程碑常量定义（beads: yimatong-bgag.1）

PRD docs/prd/pilot-learning-retrospective.md §4.1 + §6.2。
里程碑是事实记录，只由系统事件首次发生写入，只增不改。
"""

from enum import StrEnum


class PilotMilestoneType(StrEnum):
    """试点里程碑类型（派生顺序即 PRD §4.1 列示顺序）。"""

    ONBOARDING = "onboarding"  # 客户开通（租户创建时间）
    BRAND_CONFIRMED = "brand_confirmed"  # 品牌确认（首次 brand_confirmed_at）
    LAUNCHED = "launched"  # 正式上线（首次 launched_at）
    FIRST_VALID_SCAN = "first_valid_scan"  # 首次有效扫码
    FIRST_CAMPAIGN_PUBLISHED = "first_campaign_published"  # 首个活动发布


class PilotMilestoneStatus(StrEnum):
    """里程碑展示状态。"""

    ACHIEVED = "achieved"  # 已达成
    NOT_ACHIEVED = "not_achieved"  # 未达成（事件尚未发生）
    INSUFFICIENT_DATA = "insufficient_data"  # 数据不足（事实源暂未捕获）


# 展示顺序（时间线展示用）
PILOT_MILESTONE_ORDER: list[PilotMilestoneType] = [
    PilotMilestoneType.ONBOARDING,
    PilotMilestoneType.BRAND_CONFIRMED,
    PilotMilestoneType.LAUNCHED,
    PilotMilestoneType.FIRST_VALID_SCAN,
    PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED,
]

# 中文展示名（时间线/前端用）
PILOT_MILESTONE_LABELS: dict[PilotMilestoneType, str] = {
    PilotMilestoneType.ONBOARDING: "客户开通",
    PilotMilestoneType.BRAND_CONFIRMED: "品牌确认",
    PilotMilestoneType.LAUNCHED: "正式上线",
    PilotMilestoneType.FIRST_VALID_SCAN: "首次扫码",
    PilotMilestoneType.FIRST_CAMPAIGN_PUBLISHED: "首个活动发布",
}
