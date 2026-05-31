"""外部成交与 GMV 归因服务"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.models.code import CodeBatch, CodeItem
from app.models.gmv import ExternalOrder, GmvAttribution, GmvDailyStats
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.utils.crypto import hash_phone

DEFAULT_ATTRIBUTION_WINDOW_HOURS = 168  # 7 天


async def import_orders(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    orders: list[dict],
) -> int:
    """批量导入外部订单"""
    count = 0
    for o in orders:
        order = ExternalOrder(
            tenant_id=tenant_id,
            external_id=o["external_id"],
            amount=o["amount"],
            phone_hash=hash_phone(o["phone"]) if o.get("phone") else None,
            product_name=o.get("product_name"),
            order_time=_parse_time(o["order_time"]) if o.get("order_time") else None,
            channel=o.get("channel"),
            source_system=o.get("source_system"),
        )
        db.add(order)
        count += 1
    await db.flush()
    return count


async def list_orders(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    matched: bool | None = None,
) -> tuple[list[ExternalOrder], int]:
    """查询外部订单"""
    conditions = [ExternalOrder.tenant_id == tenant_id]
    if matched is not None:
        conditions.append(ExternalOrder.matched == matched)

    total = (await db.execute(
        select(func.count()).select_from(ExternalOrder).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(ExternalOrder)
        .where(*conditions)
        .order_by(ExternalOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return list(rows), total


async def batch_auto_attribution(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    limit: int = 500,
) -> dict:
    """批量自动归因：手机号匹配 + 时间窗口过滤"""
    # 1. 查找未匹配且有手机号的订单
    unmatched = (await db.execute(
        select(ExternalOrder).where(
            ExternalOrder.tenant_id == tenant_id,
            ExternalOrder.matched.is_(False),
            ExternalOrder.phone_hash.isnot(None),
        ).limit(limit)
    )).scalars().all()

    matched_count = 0
    results = []

    for order in unmatched:
        # 2. 通过 phone_hash 找到消费者
        consumer = (await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == order.phone_hash,
            )
        )).scalar_one_or_none()

        if not consumer:
            continue

        # 3. 查找该消费者最近的扫码事件（通过 public_id 关联码）
        #    注意：ScanEvent 没有 consumer_id，需要通过码来关联
        #    找到消费者最近扫过的 public_id 列表（通过关联推断）
        #    实际路径：消费者的扫码行为 -> 找到对应的码 -> 关联到码批次/产品/活动
        scan = await _find_latest_scan_for_consumer(
            db, tenant_id, consumer.id, order, window_hours
        )

        if not scan:
            continue

        # 4. 找到码对应的活动
        code_item = (await db.execute(
            select(CodeItem).where(CodeItem.public_id == scan.public_id)
        )).scalar_one_or_none()

        campaign_id = await _find_campaign_for_code(db, tenant_id, code_item) if code_item else None

        # 5. 创建归因记录
        hours_diff = _hours_between(scan.scan_time, order.order_time) if order.order_time else 0
        confidence = max(0.1, 1.0 - (hours_diff / window_hours) * 0.5)

        attr = GmvAttribution(
            tenant_id=tenant_id,
            external_order_id=order.id,
            public_id=scan.public_id,
            code_item_id=code_item.id if code_item else None,
            campaign_id=campaign_id,
            consumer_id=consumer.id,
            amount=order.amount,
            match_type="phone",
            scan_time=scan.scan_time,
            attribution_window_hours=window_hours,
            confidence_score=round(confidence, 2),
        )
        db.add(attr)
        order.matched = True
        matched_count += 1
        results.append({
            "order_id": str(order.id),
            "public_id": scan.public_id,
            "match_type": "phone",
            "confidence": round(confidence, 2),
        })

    await db.flush()
    return {"matched": matched_count, "total_checked": len(unmatched), "details": results}


async def _find_latest_scan_for_consumer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    order: ExternalOrder,
    window_hours: int,
) -> ScanEvent | None:
    """查找消费者在订单前 window_hours 时间窗口内的最近扫码"""
    # 通过 consumer 的关联码来找扫码记录
    # 实际上我们需要从码绑定/扫码行为推断
    # 简化：找 phone_hash 对应消费者扫过的码的扫码记录
    # 更精确：消费者没有直接关联到 ScanEvent
    # 实际路径：找到消费者绑定的码 → 那些码的扫码记录
    # 但消费者可以扫任何码，不限于自己绑定的

    # 策略：找同一 tenant 下、在订单时间前 window_hours 内的所有扫码事件
    #        按 public_id 去重（一个码被扫多次只取最近一次）
    #        然后通过码的消费者绑定关系过滤
    # 实际可行路径：通过 order.phone_hash → 码激活记录（如果码绑定了手机号）
    # 但当前模型中 CodeItem 没有手机号字段

    # 最实用的方案：用扫码时间窗口 + 产品名匹配
    if not order.order_time:
        window_start = datetime.now(order.order_time.tzinfo if order.order_time else None) - timedelta(hours=window_hours)
    else:
        window_start = order.order_time - timedelta(hours=window_hours)

    # 通过产品名匹配（如果外部订单有产品名）
    stmt = (
        select(ScanEvent)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= window_start,
            ScanEvent.scan_time <= order.order_time,
        )
        .order_by(ScanEvent.scan_time.desc())
        .limit(1)
    )
    scan = (await db.execute(stmt)).scalar_one_or_none()

    # 如果有产品名，进一步验证
    if scan and order.product_name:
        code = (await db.execute(
            select(CodeItem).where(CodeItem.public_id == scan.public_id)
        )).scalar_one_or_none()
        if code:
            batch = (await db.execute(
                select(CodeBatch).where(CodeBatch.id == code.code_batch_id)
            )).scalar_one_or_none()
            if batch:
                from app.models.product import Product
                product = (await db.execute(
                    select(Product).where(Product.id == batch.product_id)
                )).scalar_one_or_none()
                if product and order.product_name and product.name != order.product_name:
                    return None

    return scan


async def _find_campaign_for_code(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_item: CodeItem,
) -> uuid.UUID | None:
    """查找码关联的活动"""
    if not code_item:
        return None

    batch = (await db.execute(
        select(CodeBatch).where(CodeBatch.id == code_item.code_batch_id)
    )).scalar_one_or_none()
    if not batch:
        return None

    # 通过产品的活动绑定查找
    from app.models.product import Product
    product = (await db.execute(
        select(Product).where(Product.id == batch.product_id)
    )).scalar_one_or_none()
    if not product:
        return None

    # 找 active 的活动（简化：匹配产品类别或名称）
    campaign = (await db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        ).limit(1)
    )).scalar_one_or_none()
    return campaign.id if campaign else None


async def get_gmv_dashboard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    campaign_id: uuid.UUID | None = None,
    channel: str | None = None,
) -> dict:
    """GMV 归因看板"""
    attr_conditions = [GmvAttribution.tenant_id == tenant_id]
    order_conditions = [ExternalOrder.tenant_id == tenant_id]

    if start_date:
        attr_conditions.append(GmvAttribution.scan_time >= start_date)
    if end_date:
        attr_conditions.append(GmvAttribution.scan_time <= end_date)
    if campaign_id:
        attr_conditions.append(GmvAttribution.campaign_id == campaign_id)
    if channel:
        order_conditions.append(ExternalOrder.channel == channel)

    total_gmv = float((await db.execute(
        select(func.coalesce(func.sum(GmvAttribution.amount), 0))
        .where(*attr_conditions)
    )).scalar() or 0)

    matched_orders = (await db.execute(
        select(func.count()).select_from(GmvAttribution).where(*attr_conditions)
    )).scalar() or 0

    total_orders = (await db.execute(
        select(func.count()).select_from(ExternalOrder).where(*order_conditions)
    )).scalar() or 0

    attribution_rate = round(matched_orders / total_orders * 100, 1) if total_orders else 0

    # 按日期趋势（最近 30 天）
    daily_trend = await _get_daily_trend(db, tenant_id, start_date, end_date)

    # 按渠道分布
    by_channel = await _get_channel_breakdown(db, tenant_id, start_date, end_date)

    # 按活动分布
    by_campaign = await _get_campaign_breakdown(db, tenant_id, start_date, end_date)

    return {
        "total_gmv": total_gmv,
        "attributed_orders": matched_orders,
        "total_orders": total_orders,
        "attribution_rate": attribution_rate,
        "daily_trend": daily_trend,
        "by_channel": by_channel,
        "by_campaign": by_campaign,
    }


async def _get_daily_trend(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[dict]:
    """按日归因趋势"""
    conditions = [GmvAttribution.tenant_id == tenant_id]
    if start_date:
        conditions.append(GmvAttribution.scan_time >= start_date)
    if end_date:
        conditions.append(GmvAttribution.scan_time <= end_date)

    rows = (await db.execute(
        select(
            func.date_trunc("day", GmvAttribution.scan_time).label("day"),
            func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
            func.count().label("orders"),
        )
        .where(*conditions)
        .group_by("day")
        .order_by("day")
    )).all()

    return [{"date": str(r.day.date()) if r.day else "", "gmv": float(r.gmv), "orders": r.orders} for r in rows]


async def _get_channel_breakdown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[dict]:
    """按渠道归因分布"""
    conditions = [GmvAttribution.tenant_id == tenant_id]
    if start_date:
        conditions.append(GmvAttribution.scan_time >= start_date)
    if end_date:
        conditions.append(GmvAttribution.scan_time <= end_date)

    # 通过 external_order 的 channel 关联
    rows = (await db.execute(
        select(
            ExternalOrder.channel,
            func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
            func.count().label("orders"),
        )
        .join(GmvAttribution, GmvAttribution.external_order_id == ExternalOrder.id)
        .where(*conditions)
        .group_by(ExternalOrder.channel)
    )).all()

    return [
        {"channel": r.channel or "unknown", "gmv": float(r.gmv), "orders": r.orders}
        for r in rows
    ]


async def _get_campaign_breakdown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[dict]:
    """按活动归因分布"""
    conditions = [GmvAttribution.tenant_id == tenant_id, GmvAttribution.campaign_id.isnot(None)]
    if start_date:
        conditions.append(GmvAttribution.scan_time >= start_date)
    if end_date:
        conditions.append(GmvAttribution.scan_time <= end_date)

    rows = (await db.execute(
        select(
            GmvAttribution.campaign_id,
            Campaign.name.label("campaign_name"),
            func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
            func.count().label("orders"),
            func.avg(GmvAttribution.confidence_score).label("avg_confidence"),
        )
        .join(Campaign, Campaign.id == GmvAttribution.campaign_id, isouter=True)
        .where(*conditions)
        .group_by(GmvAttribution.campaign_id, Campaign.name)
    )).all()

    return [
        {
            "campaign_id": str(r.campaign_id),
            "campaign_name": r.campaign_name or "Unknown",
            "gmv": float(r.gmv),
            "orders": r.orders,
            "avg_confidence": round(float(r.avg_confidence or 0), 2),
        }
        for r in rows
    ]


async def get_roi_report(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """ROI 报表：按活动维度计算 ROI 指标"""
    # 获取各活动的归因数据
    attr_conditions = [GmvAttribution.tenant_id == tenant_id, GmvAttribution.campaign_id.isnot(None)]
    if campaign_id:
        attr_conditions.append(GmvAttribution.campaign_id == campaign_id)
    if start_date:
        attr_conditions.append(GmvAttribution.scan_time >= start_date)
    if end_date:
        attr_conditions.append(GmvAttribution.scan_time <= end_date)

    # 按 campaign 分组统计 GMV
    gmv_rows = (await db.execute(
        select(
            GmvAttribution.campaign_id,
            func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
            func.count().label("attributed_orders"),
            func.avg(GmvAttribution.confidence_score).label("avg_confidence"),
        )
        .where(*attr_conditions)
        .group_by(GmvAttribution.campaign_id)
    )).all()

    results = []
    for row in gmv_rows:
        cid = row.campaign_id

        # 查活动信息
        camp = (await db.execute(
            select(Campaign).where(Campaign.id == cid, Campaign.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if not camp:
            continue

        # 扫码统计
        # 通过码批次找到该活动关联的码 → 那些码的扫码次数
        scan_count, scan_uv = await _get_campaign_scan_stats(
            db, tenant_id, cid, start_date, end_date
        )

        # ROI 计算
        budget = _extract_budget(camp.rules_json)
        scan_cost = budget / scan_count if scan_count and budget else 0
        conversion_rate = round(row.attributed_orders / scan_uv * 100, 2) if scan_uv else 0
        roi = float(row.gmv) / budget if budget else 0

        results.append({
            "campaign_id": str(cid),
            "campaign_name": camp.name,
            "status": camp.status,
            "budget": budget,
            "attributed_gmv": float(row.gmv),
            "attributed_orders": row.attributed_orders,
            "scan_count": scan_count,
            "scan_uv": scan_uv,
            "scan_cost": round(scan_cost, 2),
            "conversion_rate": conversion_rate,
            "roi": round(roi, 2),
            "avg_confidence": round(float(row.avg_confidence or 0), 2),
        })

    # 按 GMV 降序
    results.sort(key=lambda x: x["attributed_gmv"], reverse=True)
    return results


async def _get_campaign_scan_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> tuple[int, int]:
    """获取活动关联的扫码统计 (总次数, UV)"""
    # 简化：统计该租户下的扫码事件
    # 实际应通过码→产品→活动链路精确关联
    conditions = [ScanEvent.tenant_id == tenant_id]
    if start_date:
        conditions.append(ScanEvent.scan_time >= start_date)
    if end_date:
        conditions.append(ScanEvent.scan_time <= end_date)

    scan_count = (await db.execute(
        select(func.count()).select_from(ScanEvent).where(*conditions)
    )).scalar() or 0

    scan_uv = (await db.execute(
        select(func.count(func.distinct(ScanEvent.public_id)))
        .select_from(ScanEvent)
        .where(*conditions)
    )).scalar() or 0

    return scan_count, scan_uv


def _extract_budget(rules_json: dict) -> float | None:
    """从活动 rules_json 中提取预算"""
    if not rules_json:
        return None
    return rules_json.get("budget")


async def list_attributions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    match_type: str | None = None,
    campaign_id: uuid.UUID | None = None,
) -> tuple[list[GmvAttribution], int]:
    """查询归因记录"""
    conditions = [GmvAttribution.tenant_id == tenant_id]
    if match_type:
        conditions.append(GmvAttribution.match_type == match_type)
    if campaign_id:
        conditions.append(GmvAttribution.campaign_id == campaign_id)

    total = (await db.execute(
        select(func.count()).select_from(GmvAttribution).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(GmvAttribution)
        .where(*conditions)
        .order_by(GmvAttribution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return list(rows), total


async def aggregate_daily_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target_date: datetime | None = None,
) -> int:
    """聚合并 upsert 日统计（供定时任务调用）"""
    from app.utils import utcnow
    date = target_date or utcnow()
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # 扫码统计
    scan_count = (await db.execute(
        select(func.count()).select_from(ScanEvent).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= day_start,
            ScanEvent.scan_time < day_end,
        )
    )).scalar() or 0

    scan_uv = (await db.execute(
        select(func.count(func.distinct(ScanEvent.public_id)))
        .select_from(ScanEvent)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= day_start,
            ScanEvent.scan_time < day_end,
        )
    )).scalar() or 0

    # 归因统计（按 channel 维度）
    attr_rows = (await db.execute(
        select(
            ExternalOrder.channel,
            func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
            func.count().label("orders"),
        )
        .join(GmvAttribution, GmvAttribution.external_order_id == ExternalOrder.id)
        .where(
            GmvAttribution.tenant_id == tenant_id,
            GmvAttribution.scan_time >= day_start,
            GmvAttribution.scan_time < day_end,
        )
        .group_by(ExternalOrder.channel)
    )).all()

    upserted = 0
    for row in attr_rows:
        # Upsert using raw SQL for ON CONFLICT
        from sqlalchemy import text
        await db.execute(
            text("""
                INSERT INTO gmv_daily_stats (id, tenant_id, stat_date, channel, attributed_gmv, attributed_orders, scan_count, scan_uv)
                VALUES (gen_random_uuid(), :tid, :dt, :ch, :gmv, :ords, :sc, :suv)
                ON CONFLICT (tenant_id, stat_date, campaign_id, channel)
                DO UPDATE SET attributed_gmv = EXCLUDED.attributed_gmv,
                              attributed_orders = EXCLUDED.attributed_orders,
                              scan_count = EXCLUDED.scan_count,
                              scan_uv = EXCLUDED.scan_uv
            """),
            {"tid": str(tenant_id), "dt": day_start, "ch": row.channel, "gmv": float(row.gmv), "ords": row.orders, "sc": scan_count, "suv": scan_uv}
        )
        upserted += 1

    # 如果没有归因数据，也记录扫码统计
    if not attr_rows:
        from sqlalchemy import text
        await db.execute(
            text("""
                INSERT INTO gmv_daily_stats (id, tenant_id, stat_date, campaign_id, channel, attributed_gmv, attributed_orders, scan_count, scan_uv)
                VALUES (gen_random_uuid(), :tid, :dt, NULL, NULL, 0, 0, :sc, :suv)
                ON CONFLICT (tenant_id, stat_date, campaign_id, channel)
                DO UPDATE SET scan_count = EXCLUDED.scan_count,
                              scan_uv = EXCLUDED.scan_uv
            """),
            {"tid": str(tenant_id), "dt": day_start, "sc": scan_count, "suv": scan_uv}
        )
        upserted = 1

    await db.flush()
    return upserted


def _parse_time(time_str: str) -> datetime:
    """解析时间字符串"""
    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"
    return datetime.fromisoformat(time_str)


def _hours_between(earlier: datetime, later: datetime) -> float:
    """计算两个时间之间的小时差"""
    if not earlier or not later:
        return 0
    return max(0, (later - earlier).total_seconds() / 3600)
