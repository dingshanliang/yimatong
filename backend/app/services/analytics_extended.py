"""扩展分析服务 — 转化漏斗、状态提醒、活动排行、最近动态"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim
from app.models.product import SKU, Product, ProductionBatch  # noqa: F401 - register CodeBatch relationships
from app.models.scan import ScanEvent
from app.models.tenant import Tenant


def _as_date(value: date | datetime | str | None) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def _as_iso_timestamp(value: date | datetime | str | None, fallback: datetime) -> str:
    if not value:
        return fallback.isoformat()
    if isinstance(value, str):
        return value
    return value.isoformat()


async def get_conversion_funnel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict:
    """yimatong-zgb1.14 AC1：7 层转化漏斗（有效访问/参与意图/权益确认/企微确认/订单/退款/净额）。

    - 有效访问（valid_visits）：scan_events.is_valid_visit=true（1.10 引入，排除 robot/失败解析）
    - 参与意图（intent）：intent_events 计数（1.10 引入，page_view/click）
    - 权益确认（claim）：benefit_claims.status=success（确认转化，1.11 固化）
    - 企微确认（wecom_confirmed）：wecom_external_contacts.status=active + welcome_code_pending=false（1.12 固化）
    - 订单（order_amount）：权威账本原始金额投影求和
    - 退款（refund_amount）：权威账本退款金额投影求和
    - 净额（net_amount）：权威账本净额投影求和（同时包含取消回冲）

    窗口：默认 days_back（相对今天）。复盘快照等需要绝对窗口的场景传
    window_start/window_end，按 [start, end] 闭区间统计（beads: yimatong-bgag.3）。
    """
    if window_start is not None and window_end is not None:
        start_dt = window_start
        end_dt = window_end
    else:
        now = datetime.now(UTC)
        start_dt = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=days_back)
        end_dt = now
    # 1. 有效访问（is_valid_visit=true，排除 robot/失败）
    visit_filters = [
        ScanEvent.tenant_id == tenant_id,
        ScanEvent.scan_time >= start_dt,
        ScanEvent.is_valid_visit.is_(True),
    ]
    visit_filters.append(ScanEvent.scan_time <= end_dt)
    valid_visit_result = await db.execute(select(func.count()).where(*visit_filters))
    valid_visit_count = valid_visit_result.scalar() or 0

    # 2. 参与意图（intent_events）
    from app.models.intent_event import IntentEvent

    intent_filters = [
        IntentEvent.tenant_id == tenant_id,
        # Public telemetry carries an advisory, bounded client timestamp.
        # Reporting windows use the server receipt clock so clients cannot
        # move intent into historical or future reporting periods.
        IntentEvent.received_at >= start_dt,
        IntentEvent.received_at <= end_dt,
    ]
    intent_result = await db.execute(select(func.count()).where(*intent_filters))
    intent_count = intent_result.scalar() or 0

    # 3. 权益确认（BenefitClaim.status=success）
    claim_filters = [
        BenefitClaim.tenant_id == tenant_id,
        BenefitClaim.created_at >= start_dt,
        BenefitClaim.status == "success",
    ]
    claim_filters.append(BenefitClaim.created_at <= end_dt)
    claim_result = await db.execute(select(func.count()).where(*claim_filters))
    claim_count = claim_result.scalar() or 0

    # 4. 企微确认（WeComExternalContact active + 非 pending）
    from app.models.wecom import WeComExternalContact, WeComExternalContactStatus

    wecom_filters = [
        WeComExternalContact.tenant_id == tenant_id,
        WeComExternalContact.added_at >= start_dt,
        WeComExternalContact.added_at <= end_dt,
        WeComExternalContact.status == WeComExternalContactStatus.ACTIVE,
        WeComExternalContact.welcome_code_pending.is_(False),
    ]
    wecom_result = await db.execute(select(func.count()).where(*wecom_filters))
    wecom_count = wecom_result.scalar() or 0

    # 5/6/7. 订单/退款/净额（external_orders）
    from app.models.gmv import ExternalOrder

    gmv_filters = [
        ExternalOrder.tenant_id == tenant_id,
        ExternalOrder.order_time >= start_dt,
    ]
    gmv_filters.append(ExternalOrder.order_time <= end_dt)
    gmv_result = await db.execute(
        select(
            func.coalesce(func.sum(ExternalOrder.ledger_original_amount), 0),
            func.coalesce(func.sum(ExternalOrder.ledger_refunded_amount), 0),
            func.coalesce(func.sum(ExternalOrder.ledger_cancelled_amount), 0),
            func.coalesce(func.sum(ExternalOrder.ledger_net_amount), 0),
            func.count().filter(ExternalOrder.ledger_net_amount.is_(None)),
        ).where(*gmv_filters)
    )
    gmv_row = gmv_result.one()
    order_amount = float(gmv_row[0] or 0)
    refund_amount = float(gmv_row[1] or 0)
    cancelled_amount = float(gmv_row[2] or 0)
    net_amount = float(gmv_row[3] or 0)
    quarantined_orders = int(gmv_row[4] or 0)

    # Cohort results use the immutable confirmation receipt and its server
    # receipt clock. Legacy/quarantined attribution rows are never counted.
    from app.models.gmv import GmvAttribution, GmvAttributionConfirmation

    cohort_visitor_count = int(
        (
            await db.execute(
                select(func.count(func.distinct(ScanEvent.visitor_id))).where(
                    ScanEvent.tenant_id == tenant_id,
                    ScanEvent.created_at >= start_dt,
                    ScanEvent.created_at <= end_dt,
                    ScanEvent.is_valid_visit.is_(True),
                    ScanEvent.visitor_id.isnot(None),
                )
            )
        ).scalar()
        or 0
    )
    collecting_cutoff = datetime.now(UTC) - timedelta(hours=720)
    collecting_visitor_count = int(
        (
            await db.execute(
                select(func.count(func.distinct(ScanEvent.visitor_id))).where(
                    ScanEvent.tenant_id == tenant_id,
                    ScanEvent.created_at >= start_dt,
                    ScanEvent.created_at <= end_dt,
                    ScanEvent.created_at > collecting_cutoff,
                    ScanEvent.is_valid_visit.is_(True),
                    ScanEvent.visitor_id.isnot(None),
                )
            )
        ).scalar()
        or 0
    )
    cohort_row = (
        await db.execute(
            select(
                func.count(GmvAttribution.id),
                func.count(func.distinct(GmvAttributionConfirmation.visitor_id)),
                func.coalesce(func.sum(GmvAttribution.amount), 0),
            )
            .join(
                GmvAttributionConfirmation,
                (GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id)
                & (GmvAttributionConfirmation.attribution_id == GmvAttribution.id),
            )
            .where(
                GmvAttribution.tenant_id == tenant_id,
                GmvAttribution.authority_status == "confirmed",
                GmvAttributionConfirmation.scan_received_at >= start_dt,
                GmvAttributionConfirmation.scan_received_at <= end_dt,
            )
        )
    ).one()
    cohort_order_count = int(cohort_row[0] or 0)
    cohort_converted_visitors = int(cohort_row[1] or 0)
    cohort_net_amount = float(cohort_row[2] or 0)

    confirmed_occurrence_orders = int(
        (
            await db.execute(
                select(func.count(GmvAttribution.id))
                .join(
                    ExternalOrder,
                    (ExternalOrder.tenant_id == GmvAttribution.tenant_id)
                    & (ExternalOrder.id == GmvAttribution.external_order_id),
                )
                .where(
                    GmvAttribution.tenant_id == tenant_id,
                    GmvAttribution.authority_status == "confirmed",
                    ExternalOrder.order_time >= start_dt,
                    ExternalOrder.order_time <= end_dt,
                )
            )
        ).scalar()
        or 0
    )
    eligible_occurrence_orders = int(
        (
            await db.execute(
                select(func.count(ExternalOrder.id)).where(
                    ExternalOrder.tenant_id == tenant_id,
                    ExternalOrder.order_time >= start_dt,
                    ExternalOrder.order_time <= end_dt,
                    ExternalOrder.ledger_net_amount.isnot(None),
                )
            )
        ).scalar()
        or 0
    )
    unattributed_order_count = max(eligible_occurrence_orders - confirmed_occurrence_orders, 0)

    cohort_complete = collecting_visitor_count == 0
    confirmed_order_visitor_rate = (
        round(cohort_converted_visitors / cohort_visitor_count * 100, 1)
        if cohort_complete and cohort_visitor_count > 0
        else None
    )

    # yimatong-zgb1.14 AC1：7 层漏斗（有效访问为分母，Decision 21）
    steps = [
        {"name": "有效访问", "value": valid_visit_count, "unit": "count", "rate": None},
        {"name": "参与意图", "value": intent_count, "unit": "count", "rate": None},
        {"name": "权益确认", "value": claim_count, "unit": "count", "rate": None},
        {"name": "企微确认", "value": wecom_count, "unit": "count", "rate": None},
        {"name": "订单总额", "value": round(order_amount, 2), "unit": "yuan", "rate": None},
        {"name": "退款总额", "value": round(refund_amount, 2), "unit": "yuan", "rate": None},
        {"name": "净成交额", "value": round(net_amount, 2), "unit": "yuan", "rate": None},
    ]

    return {
        "period_days": days_back,
        "window_start": start_dt.isoformat(),
        "window_end": end_dt.isoformat(),
        "report_type": "result_occurrence",
        "conversion_rates_available": False,
        # AC1：7 层漏斗
        "valid_visits": valid_visit_count,
        "intent_events": intent_count,
        "confirmed_claims": claim_count,
        "confirmed_wecom": wecom_count,
        "order_amount": round(order_amount, 2),
        "refund_amount": round(refund_amount, 2),
        "cancelled_amount": round(cancelled_amount, 2),
        "net_amount": round(net_amount, 2),
        "order_data_quality": "incomplete" if quarantined_orders or unattributed_order_count else "complete",
        "quarantined_order_count": quarantined_orders,
        "unattributed_order_count": unattributed_order_count,
        "visitor_cohort": {
            "window_start": start_dt.isoformat(),
            "window_end": end_dt.isoformat(),
            "attribution_window_hours": 720,
            "status": "complete" if cohort_complete else "collecting",
            "visitors": cohort_visitor_count,
            "collecting_visitors": collecting_visitor_count,
            "confirmed_order_visitors": cohort_converted_visitors,
            "confirmed_orders": cohort_order_count,
            "confirmed_net_amount": round(cohort_net_amount, 2),
            "confirmed_order_visitor_rate": confirmed_order_visitor_rate,
        },
        # 兼容旧字段（scan_count 改为 valid_visits 的别名，Decision 21）
        "scan_count": valid_visit_count,
        "claim_count": claim_count,
        "claim_rate": None,
        "gmv_amount": round(net_amount, 2),  # net GMV（含退款冲减）
        "gmv_rate": None,
        "steps": steps,
    }


async def get_alerts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """获取状态提醒：码余量、活动异常、套餐到期"""
    alerts: list[dict] = []
    today = date.today()

    # 1. 码余量预警
    from app.models.code import CodeItem, CodeItemStatus

    total_codes = await db.execute(select(func.count()).where(CodeItem.tenant_id == tenant_id))
    used_codes = await db.execute(
        select(func.count()).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.status.in_([CodeItemStatus.activated, CodeItemStatus.bound]),
        )
    )
    total = total_codes.scalar() or 0
    used = used_codes.scalar() or 0
    remaining = total - used
    if 0 < remaining < 5000:
        alerts.append(
            {
                "type": "code_quota",
                "level": "warning" if remaining < 1000 else "info",
                "message": f"码余量仅剩 {remaining:,}，建议尽快补充"
                if remaining < 1000
                else f"码余量 {remaining:,}，请关注",
                "action_url": "/codes",
            }
        )

    # 2. 活动状态提醒
    from app.models.campaign import Campaign

    active_campaigns = await db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        )
    )
    active_count = 0
    for campaign in active_campaigns.scalars().all():
        active_count += 1
        end_date = _as_date(campaign.end_at)
        if end_date:
            days_left = (end_date - today).days
            if 0 < days_left <= 7:
                alerts.append(
                    {
                        "type": "campaign_status",
                        "level": "info",
                        "message": f"活动「{campaign.name}」将于 {days_left} 天后到期",
                        "action_url": f"/campaigns/{campaign.id}",
                    }
                )

    if active_count > 0:
        alerts.append(
            {
                "type": "campaign_status",
                "level": "info",
                "message": f"当前 {active_count} 个活动进行中",
                "action_url": "/campaigns",
            }
        )

    # 3. 套餐到期提醒
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant and tenant.plan_expires_at:
        days_to_expire = (tenant.plan_expires_at.date() - today).days
        if 0 < days_to_expire <= 30:
            alerts.append(
                {
                    "type": "plan_expiry",
                    "level": "warning" if days_to_expire <= 15 else "info",
                    "message": f"套餐将于 {days_to_expire} 天后到期，请及时续费",
                    "action_url": "/settings/tenant",
                }
            )

    level_order = {"error": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: level_order.get(a["level"], 3))

    return {"alerts": alerts[:5]}


async def get_campaign_ranking(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
    limit: int = 5,
) -> dict:
    """Rank confirmed campaign results without an untrusted scan denominator."""
    from app.models.campaign import Campaign

    today = date.today()
    start_dt = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days_back)

    campaigns_result = await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_id))
    campaigns = campaigns_result.scalars().all()

    items = []
    for campaign in campaigns:
        claim_result = await db.execute(
            select(func.count()).where(
                BenefitClaim.tenant_id == tenant_id,
                BenefitClaim.campaign_id == campaign.id,
                BenefitClaim.created_at >= start_dt,
                BenefitClaim.status == "success",
            )
        )
        claim_count = claim_result.scalar() or 0

        items.append(
            {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "campaign_status": campaign.status,
                "scan_count": None,
                "claim_count": claim_count,
                "conversion_rate": None,
                "conversion_status": "unavailable",
            }
        )

    items.sort(key=lambda x: x["claim_count"], reverse=True)

    return {"items": items[:limit], "total": len(items)}


async def get_recent_events(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 5,
) -> dict:
    """获取最近业务事件时间线"""
    events: list[dict] = []
    today = date.today()
    yesterday = today - timedelta(days=1)
    yesterday_dt = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=UTC)
    today_dt = datetime(today.year, today.month, today.day, tzinfo=UTC)

    # 1. 昨日扫码变化
    from app.models.analytics import DailyScanStats

    yesterday_stats = await db.execute(
        select(DailyScanStats).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date == yesterday,
        )
    )
    ys = yesterday_stats.scalar_one_or_none()
    if ys and ys.total_scans > 0:
        day_before = await db.execute(
            select(DailyScanStats).where(
                DailyScanStats.tenant_id == tenant_id,
                DailyScanStats.date == yesterday - timedelta(days=1),
            )
        )
        db_stats = day_before.scalar_one_or_none()
        if db_stats and db_stats.total_scans > 0:
            change = round((ys.total_scans - db_stats.total_scans) / db_stats.total_scans * 100, 1)
            if abs(change) >= 10:
                direction = "增长" if change > 0 else "下降"
                events.append(
                    {
                        "event_type": "scan_surge",
                        "message": f"昨日扫码{direction} {abs(change)}%（{ys.total_scans} 次）",
                        "timestamp": yesterday_dt.isoformat(),
                        "action_url": "/",
                    }
                )

    # 2. 进行中的活动
    from app.models.campaign import Campaign

    active_campaigns = await db.execute(
        select(Campaign)
        .where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        )
        .limit(3)
    )
    for campaign in active_campaigns.scalars().all():
        events.append(
            {
                "event_type": "campaign_status_change",
                "message": f"活动「{campaign.name}」进行中",
                "timestamp": _as_iso_timestamp(campaign.start_at, today_dt),
                "action_url": f"/campaigns/{campaign.id}",
            }
        )

    events.sort(key=lambda e: e["timestamp"], reverse=True)

    return {"events": events[:limit]}
