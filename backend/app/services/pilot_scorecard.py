"""运营 scorecard 快照服务（beads: yimatong-bgag.3，PRD pilot-learning-retrospective §4.4）。

6 指标口径，复用既有事实源，不重新定义：
- 开通→上线时长、上线→首扫时长：复用票 1 的里程碑派生（app.services.pilot_milestone）。
- 有效访问、权益确认率、企微确认率、订单/净GMV：复用七层漏斗口径
  （app.services.analytics_extended.get_conversion_funnel），按窗口 days_back 对齐。

物化原则（PRD §7）：快照在生成时点计算后存储，后续源数据变化（如退款回冲）不改写
已归档快照。指标源数据缺失时返回 value=null + status="insufficient_data"，
不得显示为 0 伪装真实结果（PRD §4.4 Failure）。
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.pilot import PilotMilestoneType
from app.models.pilot_milestone import PilotMilestone
from app.services.analytics_extended import get_conversion_funnel

INSUFFICIENT = "insufficient_data"
COMPUTED = "computed"


def _metric(value, status: str = COMPUTED, **extra) -> dict:
    """组装单个指标项。"""
    item = {"value": value, "status": status}
    item.update(extra)
    return item


async def _milestone_durations(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """从已持久化的里程碑取开通→上线、上线→首扫时长（秒）。缺失记数据不足。"""

    rows = await db.execute(select(PilotMilestone).where(PilotMilestone.tenant_id == tenant_id))
    by_type: dict = {r.milestone_type: r for r in rows.scalars().all()}

    def _duration(frm: PilotMilestoneType, to: PilotMilestoneType) -> dict:
        f = by_type.get(frm)
        t = by_type.get(to)
        if f is None or t is None or f.achieved_at is None or t.achieved_at is None:
            return _metric(None, INSUFFICIENT)
        return _metric((t.achieved_at - f.achieved_at).total_seconds(), unit="seconds")

    return {
        "onboarding_to_launch": _duration(PilotMilestoneType.ONBOARDING, PilotMilestoneType.LAUNCHED),
        "launch_to_first_scan": _duration(PilotMilestoneType.LAUNCHED, PilotMilestoneType.FIRST_VALID_SCAN),
    }


async def build_scorecard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """生成 6 指标 scorecard 快照。

    窗口由 (window_start, window_end) 绝对决定；漏斗按该闭区间统计
    （传入 window_start/window_end，不再用 days_back 相对今天），
    保证同租户同期复盘数字一致（PRD §4.4）。
    """
    window_days = max(1, (window_end.date() - window_start.date()).days)

    # 里程碑时长（全量，不受窗口限制，PRD §4.4）
    durations = await _milestone_durations(db, tenant_id)

    # 漏斗口径（本期绝对窗口 [window_start, window_end]）
    funnel = await get_conversion_funnel(db, tenant_id, window_start=window_start, window_end=window_end)

    valid_visits = funnel.get("valid_visits", 0)
    claim_count = funnel.get("confirmed_claims", 0)
    wecom_count = funnel.get("confirmed_wecom", 0)
    order_amount = funnel.get("order_amount", 0)
    net_amount = funnel.get("net_amount", 0)

    def _rate(num: int | float, denom: int) -> dict:
        """率指标：分母为 0（无有效访问）记数据不足，不显示 0%。"""
        if denom <= 0:
            return _metric(None, INSUFFICIENT, denominator=denom)
        return _metric(round(num / denom * 100, 2), unit="percent", denominator=denom, numerator=num)

    def _gmv(amount: float) -> dict:
        """GMV：无订单数据（amount 为 0 且非真实）记数据不足。"""
        if amount <= 0:
            return _metric(None, INSUFFICIENT)
        return _metric(round(amount, 2), unit="yuan")

    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_days": window_days,
        "onboarding_to_launch": durations["onboarding_to_launch"],
        "launch_to_first_scan": durations["launch_to_first_scan"],
        "valid_visits": _metric(valid_visits, unit="count"),
        "claim_rate": _rate(claim_count, valid_visits),
        "wecom_rate": _rate(wecom_count, valid_visits),
        "net_gmv": _gmv(net_amount),
        "order_amount": _gmv(order_amount),
        # 原始漏斗计数（便于审计回查，PRD §4.4 透明口径）
        "funnel_raw": {
            "valid_visits": valid_visits,
            "confirmed_claims": claim_count,
            "confirmed_wecom": wecom_count,
            "order_amount": order_amount,
            "refund_amount": funnel.get("refund_amount", 0),
            "net_amount": net_amount,
        },
    }
