"""试点复盘常量定义（beads: yimatong-bgag.2，PRD pilot-learning-retrospective §4.2/§6.1）。"""

from enum import StrEnum


class RetrospectiveStatus(StrEnum):
    """复盘存储状态机（PRD §6.1）。"""

    PENDING = "pending"  # 待填写
    COMPLETED = "completed"  # 已完成（快照冻结）


# 复盘到期期次（PRD §4.2：上线后第 7/14/30 天）
RETRO_PERIOD_DAYS: list[int] = [7, 14, 30]


# 计算态标签（不落库，由读取层根据 now vs next_review_date 派生）
RETO_STATE_OVERDUE = "overdue"  # 逾期未填写（pending 且超过 next_review_date）
RETO_STATE_OVERDUE_COMPLETED = "overdue_completed"  # 逾期后补填完成


# 中文展示名
RETROSPECTIVE_STATUS_LABELS: dict[str, str] = {
    RetrospectiveStatus.PENDING.value: "待填写",
    RetrospectiveStatus.COMPLETED.value: "已完成",
    RETO_STATE_OVERDUE: "逾期",
    RETO_STATE_OVERDUE_COMPLETED: "逾期完成",
}
