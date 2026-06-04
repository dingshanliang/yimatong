"""扩展分析服务 — 转化漏斗、状态提醒、活动排行、最近动态"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.models.tenant import Tenant


async def get_conversion_funnel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
) -> dict:
    """获取转化漏斗数据：扫码→领券→留资→加私域→GMV"""
    today = date.today()
    start_dt = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days_back)

    # 1. 扫码量
    scan_result = await db.execute(
        select(func.count()).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= start_dt,
        )
    )
    scan_count = scan_result.scalar() or 0

    # 2. 领券量
    claim_result = await db.execute(
        select(func.count()).where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.created_at >= start_dt,
            BenefitClaim.status == "success",
        )
    )
    claim_count = claim_result.scalar() or 0

    # 3. 留资量（有 phone_hash 的消费者）
    signup_result = await db.execute(
        select(func.count()).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.phone_hash.isnot(None),
        )
    )
    signup_count = signup_result.scalar() or 0

    # 4. 加私域量（微信扫码去重用户）
    private_domain_result = await db.execute(
        select(func.count(ScanEvent.public_id.distinct())).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= start_dt,
            ScanEvent.environment == "wechat",
        )
    )
    private_domain_count = private_domain_result.scalar() or 0

    # 5. GMV（从 gmv 模型获取归因订单金额）
    gmv_amount = 0.0
    try:
        from app.models.gmv import ExternalOrder

        gmv_result = await db.execute(
            select(func.coalesce(func.sum(ExternalOrder.order_amount), 0)).where(
                ExternalOrder.tenant_id == tenant_id,
                ExternalOrder.order_time >= start_dt,
            )
        )
        gmv_amount = float(gmv_result.scalar() or 0)
    except Exception:
        pass

    def safe_rate(count: int, total: int) -> float:
        return round(count / total * 100, 1) if total > 0 else 0.0

    steps = [
        {"name": "扫码", "value": scan_count, "rate": 100.0},
        {"name": "领券", "value": claim_count, "rate": safe_rate(claim_count, scan_count)},
        {"name": "留资", "value": signup_count, "rate": safe_rate(signup_count, scan_count)},
        {"name": "加私域", "value": private_domain_count, "rate": safe_rate(private_domain_count, scan_count)},
        {
            "name": "购买(GMV)",
            "value": int(gmv_amount),
            "rate": safe_rate(int(gmv_amount), scan_count) if gmv_amount > 0 else 0.0,
        },
    ]

    return {
        "period_days": days_back,
        "scan_count": scan_count,
        "claim_count": claim_count,
        "claim_rate": safe_rate(claim_count, scan_count),
        "signup_count": signup_count,
        "signup_rate": safe_rate(signup_count, scan_count),
        "private_domain_count": private_domain_count,
        "private_domain_rate": safe_rate(private_domain_count, scan_count),
        "gmv_amount": gmv_amount,
        "gmv_rate": safe_rate(int(gmv_amount), scan_count) if gmv_amount > 0 else 0.0,
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
        if campaign.end_at:
            days_left = (campaign.end_at.date() - today).days
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
    """按转化率（领券量/扫码量）排序的活动排行"""
    from app.models.campaign import Campaign
    from app.models.code import CodeBatch, CodeItem

    today = date.today()
    start_dt = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days_back)

    campaigns_result = await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_id))
    campaigns = campaigns_result.scalars().all()

    items = []
    for campaign in campaigns:
        scan_count = 0
        claim_count = 0

        if campaign.product_id:
            batch_result = await db.execute(
                select(CodeBatch.id).where(
                    CodeBatch.tenant_id == tenant_id,
                    CodeBatch.product_id == campaign.product_id,
                )
            )
            batch_ids = [row[0] for row in batch_result.all()]

            if batch_ids:
                code_result = await db.execute(
                    select(CodeItem.public_id).where(
                        CodeItem.tenant_id == tenant_id,
                        CodeItem.code_batch_id.in_(batch_ids),
                    )
                )
                public_ids = [row[0] for row in code_result.all()]

                if public_ids:
                    scan_result = await db.execute(
                        select(func.count()).where(
                            ScanEvent.tenant_id == tenant_id,
                            ScanEvent.public_id.in_(public_ids),
                            ScanEvent.scan_time >= start_dt,
                        )
                    )
                    scan_count = scan_result.scalar() or 0

        claim_result = await db.execute(
            select(func.count()).where(
                BenefitClaim.tenant_id == tenant_id,
                BenefitClaim.campaign_id == campaign.id,
                BenefitClaim.created_at >= start_dt,
                BenefitClaim.status == "success",
            )
        )
        claim_count = claim_result.scalar() or 0

        conversion_rate = round(claim_count / scan_count * 100, 1) if scan_count > 0 else 0.0

        items.append(
            {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "campaign_status": campaign.status,
                "scan_count": scan_count,
                "claim_count": claim_count,
                "conversion_rate": conversion_rate,
            }
        )

    items.sort(key=lambda x: x["conversion_rate"], reverse=True)

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
                "timestamp": campaign.start_at.isoformat() if campaign.start_at else today_dt.isoformat(),
                "action_url": f"/campaigns/{campaign.id}",
            }
        )

    events.sort(key=lambda e: e["timestamp"], reverse=True)

    return {"events": events[:limit]}
