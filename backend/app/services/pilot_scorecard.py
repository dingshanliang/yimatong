"""运营 scorecard 快照服务（beads: yimatong-bgag.3 / bgag.8，PRD pilot-learning-retrospective §4.4）。

6 指标口径，复用既有事实源，不重新定义（PRD §6.3：复用七层漏斗与归因快照口径）：
- 开通→上线时长、上线→首扫时长：复用票 1 的里程碑派生（app.services.pilot_milestone）。
- 有效访问、权益确认率、企微确认率：复用七层漏斗口径
  （app.services.analytics_extended.get_conversion_funnel），按本期窗口对齐。
- 订单/净GMV：PRD §4.4 row 6 窗口 = "本期窗口 + 归因窗口"，来源 = "归因快照"。
  复用 GmvAttribution（归因快照，每行的 attribution_window_hours 已在写入时固化
  扫码→下单延迟），按 scan_time 在本期窗口 [window_start, window_end] 聚合 amount
  （amount 已是退款回冲后的净额，见 gmv.refund_order）。

物化原则（PRD §7）：快照在生成时点计算后存储，后续源数据变化（如退款回冲）不改写
已归档快照。指标源数据缺失时返回 value=null + status="insufficient_data"，
不得显示为 0 伪装真实结果（PRD §4.4 Failure）。
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.pilot import PilotMilestoneType
from app.models.gmv import GmvAttribution
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

    # 漏斗口径（本期绝对窗口 [window_start, window_end]）—— 有效访问/权益确认/企微确认
    funnel = await get_conversion_funnel(db, tenant_id, window_start=window_start, window_end=window_end)

    valid_visits = funnel.get("valid_visits", 0)
    claim_count = funnel.get("confirmed_claims", 0)
    wecom_count = funnel.get("confirmed_wecom", 0)

    # 订单/净GMV（PRD §4.4 row 6：窗口 = 本期窗口 + 归因窗口，来源 = 归因快照）。
    # 复用 GmvAttribution（归因快照）：每行的 attribution_window_hours 在写入时已固化
    # 扫码→下单延迟，故按 scan_time 在本期窗口 [window_start, window_end] 聚合 amount
    # 即自然包含"本期窗口 + 归因窗口"内的延迟下单（PRD §6.3 复用归因快照口径）。
    # amount 已是退款回冲后的净额（见 gmv.refund_order）。
    gmv = await _gmv_from_attribution_snapshot(db, tenant_id, window_start, window_end)
    net_amount = gmv["net_amount"]
    attributed_orders = gmv["attributed_orders"]

    def _rate(num: int | float, denom: int) -> dict:
        """率指标：分母为 0（无有效访问）记数据不足，不显示 0%。"""
        if denom <= 0:
            return _metric(None, INSUFFICIENT, denominator=denom)
        return _metric(round(num / denom * 100, 2), unit="percent", denominator=denom, numerator=num)

    def _gmv(amount: float) -> dict:
        """GMV：无归因订单（amount 为 0 且非真实）记数据不足。"""
        if amount <= 0:
            return _metric(None, INSUFFICIENT)
        return _metric(round(amount, 2), unit="yuan")

    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_days": window_days,
        # GMV 来源 = 归因快照（PRD §4.4 row 6 / §6.3）；透明记录口径便于审计
        "gmv_source": "gmv_attributions",
        "gmv_scan_window_end": window_end.isoformat(),
        "onboarding_to_launch": durations["onboarding_to_launch"],
        "launch_to_first_scan": durations["launch_to_first_scan"],
        "valid_visits": _metric(valid_visits, unit="count"),
        "claim_rate": _rate(claim_count, valid_visits),
        "wecom_rate": _rate(wecom_count, valid_visits),
        "net_gmv": _gmv(net_amount),
        "order_amount": _gmv(net_amount),  # 归因快照 amount 已为净额；order/净同口径
        # 原始漏斗计数（便于审计回查，PRD §4.4 透明口径）
        "funnel_raw": {
            "valid_visits": valid_visits,
            "confirmed_claims": claim_count,
            "confirmed_wecom": wecom_count,
            # 本期窗口 external_orders 计数（不含归因，供对比）
            "order_amount_period": funnel.get("order_amount", 0),
            "refund_amount_period": funnel.get("refund_amount", 0),
            "net_amount_period": funnel.get("net_amount", 0),
            # 归因快照净额（scorecard 实际使用的 GMV 口径，PRD §4.4 row 6）
            "attributed_net_amount": net_amount,
            "attributed_orders": attributed_orders,
        },
    }


async def _gmv_from_attribution_snapshot(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """订单/净GMV（PRD §4.4 row 6：来源 = 归因快照 GmvAttribution）。

    按 scan_time 在本期窗口 [window_start, window_end] 聚合 GmvAttribution.amount。
    每行 amount 已是退款回冲后的净额（见 gmv.refund_order），且 attribution_window_hours
    在写入时固化，故"本期窗口 + 归因窗口"的延迟下单已通过 scan_time 隶属本期窗口自然纳入。
    """
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(GmvAttribution.amount), 0),
                func.count(GmvAttribution.id),
            ).where(
                GmvAttribution.tenant_id == tenant_id,
                GmvAttribution.scan_time >= window_start,
                GmvAttribution.scan_time <= window_end,
            )
        )
    ).one()
    net_amount = float(row[0] or 0)
    attributed_orders = int(row[1] or 0)
    return {
        "net_amount": round(net_amount, 2),
        "attributed_orders": attributed_orders,
    }
