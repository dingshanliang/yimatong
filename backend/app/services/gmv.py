"""外部成交与 GMV 归因服务"""

import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.campaign import Campaign
from app.models.gmv import ExternalOrder, GmvAttribution, GmvAttributionConfirmation
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.models.visitor import AnonymousVisitor
from app.utils.crypto import hash_phone

DEFAULT_ATTRIBUTION_WINDOW_HOURS = 168  # 7 天


async def import_orders(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    orders: list[dict],
    actor_id: str | None = None,
) -> dict:
    """批量导入外部订单（yimatong-zgb1.13：逐行去重 + 校验 + 审计）。

    返回 ``{created, skipped_duplicates, failed, errors}``：
    - created：成功导入的行数
    - skipped_duplicates：同 (source_system, external_id) 已存在的重复行
    - failed：校验失败的行数（无效金额/缺业务键）
    - errors：每条失败的原因列表（按行）
    """
    result = {"created": 0, "skipped_duplicates": 0, "failed": 0, "errors": []}

    for idx, o in enumerate(orders):
        # 校验：金额必须是正数
        amount = o.get("amount")
        if amount is None or not isinstance(amount, int | float) or amount <= 0:
            result["failed"] += 1
            result["errors"].append({"row": idx, "reason": "invalid_amount", "external_id": o.get("external_id")})
            continue
        # 校验：external_id 必填（业务键）
        external_id = o.get("external_id")
        if not external_id:
            result["failed"] += 1
            result["errors"].append({"row": idx, "reason": "missing_external_id"})
            continue
        # 校验：币种（默认 CNY，允许常见 ISO 4217）
        currency = str(o.get("currency", "CNY")).upper()
        if len(currency) != 3:
            result["failed"] += 1
            result["errors"].append({"row": idx, "reason": "invalid_currency", "external_id": external_id})
            continue

        source_system = o.get("source_system") or "unknown"

        # 去重：同 (tenant, source_system, external_id) 已存在则跳过
        existing = await db.execute(
            select(ExternalOrder.id).where(
                ExternalOrder.tenant_id == tenant_id,
                ExternalOrder.source_system == source_system,
                ExternalOrder.external_id == external_id,
            )
        )
        if existing.scalar_one_or_none():
            result["skipped_duplicates"] += 1
            continue

        order = ExternalOrder(
            tenant_id=tenant_id,
            external_id=external_id,
            amount=float(amount),
            phone_hash=hash_phone(o["phone"]) if o.get("phone") else None,
            product_name=o.get("product_name"),
            order_time=_parse_time(o["order_time"]) if o.get("order_time") else None,
            channel=o.get("channel"),
            source_system=source_system,
            currency=currency,
            status="paid",
            refund_amount=0.0,
        )
        db.add(order)
        result["created"] += 1

    await db.flush()
    return result


async def refund_order(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    refund_amount: float,
    partial: bool = False,
    actor_id: str | None = None,
) -> dict:
    """yimatong-zgb1.13 Decision 30：订单退款，冲减净 GMV，保留原始流水。

    - 全额退款（partial=False）：status=refunded, refund_amount=amount
    - 部分退款（partial=True）：status=partially_refunded, refund_amount 累加
    - 保留原始订单行（不删除），审计通过 status + refund_amount + updated_at 体现。
    """
    result = await db.execute(
        select(ExternalOrder).where(ExternalOrder.id == order_id, ExternalOrder.tenant_id == tenant_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return {"status": "not_found"}

    if refund_amount <= 0 or refund_amount > order.amount - order.refund_amount + refund_amount:
        # 退款金额不能超过订单金额（含已退）
        if refund_amount > order.amount:
            return {"status": "invalid_refund_amount"}

    if partial:
        order.refund_amount += refund_amount
        order.status = "partially_refunded"
    else:
        order.refund_amount = order.amount
        order.status = "refunded"

    # 同步更新已存在的 GmvAttribution（如有）
    attr_result = await db.execute(select(GmvAttribution).where(GmvAttribution.external_order_id == order_id))
    for attr in attr_result.scalars():
        attr.amount = order.amount - order.refund_amount  # net amount

    await db.flush()
    return {"status": "ok", "net_amount": order.amount - order.refund_amount}


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

    total = (await db.execute(select(func.count()).select_from(ExternalOrder).where(*conditions))).scalar() or 0

    rows = (
        (
            await db.execute(
                select(ExternalOrder)
                .where(*conditions)
                .order_by(ExternalOrder.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def batch_auto_attribution(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    limit: int = 500,
) -> dict:
    """Confirm orders only from an exact consumer-bound valid scan.

    PostgreSQL owns the final proof, idempotency, and write. Other dialects do
    not emulate confirmation because doing so would restore the unsafe
    application-only attribution boundary.
    """
    if db.get_bind().dialect.name != "postgresql":
        return {"matched": 0, "total_checked": 0, "details": [], "authority_unavailable": True}
    # 1. 查找未匹配且有手机号的订单
    unmatched = (
        (
            await db.execute(
                select(ExternalOrder)
                .where(
                    ExternalOrder.tenant_id == tenant_id,
                    ExternalOrder.matched.is_(False),
                    ExternalOrder.phone_hash.isnot(None),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    matched_count = 0
    results = []

    for order in unmatched:
        # 2. 通过 phone_hash 找到消费者
        consumer = (
            await db.execute(
                select(ConsumerProfile).where(
                    ConsumerProfile.tenant_id == tenant_id,
                    ConsumerProfile.phone_hash == order.phone_hash,
                )
            )
        ).scalar_one_or_none()

        if not consumer:
            continue

        # The candidate query follows the first-party identity edge. The DB
        # authority repeats and locks this proof before recording confirmation.
        scan = await _find_latest_scan_for_consumer(db, tenant_id, consumer.id, order, window_hours)

        if not scan:
            continue

        payload_digest = hashlib.sha256(
            f"{tenant_id}:{order.id}:{consumer.id}:{scan.id}:{scan.scan_time.isoformat()}:{window_hours}".encode()
        ).hexdigest()
        try:
            confirmed = await confirm_gmv_attribution(
                db,
                tenant_id=tenant_id,
                auth_session_id=auth_session_id,
                order_id=order.id,
                consumer_id=consumer.id,
                scan_event_id=scan.id,
                scan_event_time=scan.scan_time,
                window_hours=window_hours,
                idempotency_key=f"auto:{order.id}",
                payload_digest=payload_digest,
            )
        except DBAPIError as exc:
            if _sqlstate(exc) in {"22023", "23503"}:
                continue
            raise
        if not confirmed["replayed"]:
            matched_count += 1
        results.append(
            {
                "order_id": str(order.id),
                "public_id": scan.public_id,
                "match_type": "verified_consumer_scan",
                "authority_status": confirmed["authority_status"],
                "replayed": confirmed["replayed"],
            }
        )

    await db.flush()
    return {"matched": matched_count, "total_checked": len(unmatched), "details": results}


async def _find_latest_scan_for_consumer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    order: ExternalOrder,
    window_hours: int,
) -> ScanEvent | None:
    """Find a valid scan through the exact tenant visitor-to-consumer edge."""
    if not order.order_time:
        return None
    window_start = order.order_time - timedelta(hours=window_hours)
    stmt = (
        select(ScanEvent)
        .join(
            AnonymousVisitor,
            (AnonymousVisitor.tenant_id == ScanEvent.tenant_id) & (AnonymousVisitor.visitor_id == ScanEvent.visitor_id),
        )
        .where(
            ScanEvent.tenant_id == tenant_id,
            AnonymousVisitor.tenant_id == tenant_id,
            AnonymousVisitor.consumer_id == consumer_id,
            ScanEvent.is_valid_visit.is_(True),
            ScanEvent.created_at >= window_start,
            ScanEvent.created_at <= order.order_time,
        )
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


async def confirm_gmv_attribution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    order_id: uuid.UUID,
    consumer_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_event_time: datetime,
    window_hours: int,
    idempotency_key: str,
    payload_digest: str,
) -> dict:
    """Call the sole database writer for confirmed GMV attribution facts."""
    row = (
        (
            await db.execute(
                text(
                    """SELECT * FROM public.confirm_gmv_attribution(
                CAST(:tenant_id AS uuid),CAST(:auth_session_id AS uuid),
                CAST(:attribution_id AS uuid),CAST(:confirmation_id AS uuid),
                CAST(:order_id AS uuid),CAST(:consumer_id AS uuid),
                CAST(:scan_event_id AS uuid),:scan_event_time,:window_hours,:idempotency_key,:payload_digest)"""
                ),
                {
                    "tenant_id": str(tenant_id),
                    "auth_session_id": str(auth_session_id),
                    "attribution_id": str(uuid7()),
                    "confirmation_id": str(uuid7()),
                    "order_id": str(order_id),
                    "consumer_id": str(consumer_id),
                    "scan_event_id": str(scan_event_id),
                    "scan_event_time": scan_event_time,
                    "window_hours": window_hours,
                    "idempotency_key": idempotency_key,
                    "payload_digest": payload_digest,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def get_gmv_dashboard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    campaign_id: uuid.UUID | None = None,
    channel: str | None = None,
) -> dict:
    """GMV 归因看板"""
    attr_conditions = [
        GmvAttribution.tenant_id == tenant_id,
        GmvAttribution.authority_status == "confirmed",
    ]
    order_conditions = [ExternalOrder.tenant_id == tenant_id, ExternalOrder.ledger_net_amount.isnot(None)]

    if start_date:
        attr_conditions.append(GmvAttributionConfirmation.scan_received_at >= start_date)
        order_conditions.append(ExternalOrder.order_time >= start_date)
    if end_date:
        attr_conditions.append(GmvAttributionConfirmation.scan_received_at <= end_date)
        order_conditions.append(ExternalOrder.order_time <= end_date)
    if campaign_id:
        attr_conditions.append(GmvAttribution.campaign_id == campaign_id)
    if channel:
        attr_conditions.append(GmvAttribution.channel_snapshot == channel)
        order_conditions.append(ExternalOrder.channel == channel)

    total_gmv = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(GmvAttribution.amount), 0))
                .join(
                    GmvAttributionConfirmation,
                    and_(
                        GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                        GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                    ),
                )
                .where(*attr_conditions)
            )
        ).scalar()
        or 0
    )

    matched_orders = (
        await db.execute(
            select(func.count())
            .select_from(GmvAttribution)
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(*attr_conditions)
        )
    ).scalar() or 0

    total_orders = (
        await db.execute(select(func.count()).select_from(ExternalOrder).where(*order_conditions))
    ).scalar() or 0

    unattributed_orders = (
        await db.execute(
            select(func.count()).select_from(ExternalOrder).where(*order_conditions, ExternalOrder.matched.is_(False))
        )
    ).scalar() or 0

    quality_conditions = [ExternalOrder.tenant_id == tenant_id, ExternalOrder.ledger_net_amount.is_(None)]
    if start_date:
        quality_conditions.append(ExternalOrder.order_time >= start_date)
    if end_date:
        quality_conditions.append(ExternalOrder.order_time <= end_date)
    if channel:
        quality_conditions.append(ExternalOrder.channel == channel)
    quarantined_orders = (
        await db.execute(select(func.count()).select_from(ExternalOrder).where(*quality_conditions))
    ).scalar() or 0

    # Attributed occurrences are cohort-anchored on trusted scan receipt time;
    # total orders are occurrence-anchored on order time. Their ratio is not a
    # conversion rate, even when both happen to use the same date bounds.
    attribution_rate = None

    daily_trend = await _get_daily_trend(db, tenant_id, start_date, end_date, campaign_id, channel)

    # 按渠道分布
    by_channel = await _get_channel_breakdown(db, tenant_id, start_date, end_date, campaign_id, channel)

    # 按活动分布
    by_campaign = await _get_campaign_breakdown(db, tenant_id, start_date, end_date, campaign_id, channel)

    return {
        "total_gmv": total_gmv,
        "attributed_orders": matched_orders,
        "total_orders": total_orders,
        "unattributed_orders": unattributed_orders,
        "quarantined_order_count": quarantined_orders,
        "order_data_quality": "incomplete" if quarantined_orders else "complete",
        "attribution_rate": attribution_rate,
        "attribution_rate_status": "unavailable_non_cohort",
        "daily_trend": daily_trend,
        "by_channel": by_channel,
        "by_campaign": by_campaign,
    }


async def _get_daily_trend(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
    campaign_id: uuid.UUID | None = None,
    channel: str | None = None,
) -> list[dict]:
    """按日归因趋势"""
    conditions = [GmvAttribution.tenant_id == tenant_id, GmvAttribution.authority_status == "confirmed"]
    if start_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at >= start_date)
    if end_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at <= end_date)
    if campaign_id:
        conditions.append(GmvAttribution.campaign_id == campaign_id)
    if channel:
        conditions.append(GmvAttribution.channel_snapshot == channel)

    rows = (
        await db.execute(
            select(
                func.date(GmvAttributionConfirmation.scan_received_at).label("day"),
                func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
                func.count().label("orders"),
            )
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(*conditions)
            .group_by("day")
            .order_by("day")
        )
    ).all()

    return [
        {
            "date": str(r.day.date()) if r.day and hasattr(r.day, "date") else (str(r.day) if r.day else ""),
            "gmv": float(r.gmv),
            "orders": r.orders,
        }
        for r in rows
    ]


async def _get_channel_breakdown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
    campaign_id: uuid.UUID | None = None,
    channel: str | None = None,
) -> list[dict]:
    """按渠道归因分布"""
    conditions = [GmvAttribution.tenant_id == tenant_id, GmvAttribution.authority_status == "confirmed"]
    if start_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at >= start_date)
    if end_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at <= end_date)
    if campaign_id:
        conditions.append(GmvAttribution.campaign_id == campaign_id)
    if channel:
        conditions.append(GmvAttribution.channel_snapshot == channel)

    rows = (
        await db.execute(
            select(
                GmvAttribution.channel_snapshot.label("channel"),
                func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
                func.count().label("orders"),
            )
            .select_from(GmvAttribution)
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(*conditions)
            .group_by(GmvAttribution.channel_snapshot)
        )
    ).all()

    return [{"channel": r.channel or "unknown", "gmv": float(r.gmv), "orders": r.orders} for r in rows]


async def _get_campaign_breakdown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
    campaign_id: uuid.UUID | None = None,
    channel: str | None = None,
) -> list[dict]:
    """按活动归因分布"""
    conditions = [
        GmvAttribution.tenant_id == tenant_id,
        GmvAttribution.authority_status == "confirmed",
        GmvAttribution.campaign_id.isnot(None),
    ]
    if start_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at >= start_date)
    if end_date:
        conditions.append(GmvAttributionConfirmation.scan_received_at <= end_date)
    if campaign_id:
        conditions.append(GmvAttribution.campaign_id == campaign_id)
    if channel:
        conditions.append(GmvAttribution.channel_snapshot == channel)

    rows = (
        await db.execute(
            select(
                GmvAttribution.campaign_id,
                Campaign.name.label("campaign_name"),
                func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
                func.count().label("orders"),
                func.avg(GmvAttribution.confidence_score).label("avg_confidence"),
            )
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .join(
                Campaign,
                and_(Campaign.tenant_id == GmvAttribution.tenant_id, Campaign.id == GmvAttribution.campaign_id),
                isouter=True,
            )
            .where(*conditions)
            .group_by(GmvAttribution.campaign_id, Campaign.name)
        )
    ).all()

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
    attr_conditions = [
        GmvAttribution.tenant_id == tenant_id,
        GmvAttribution.authority_status == "confirmed",
        GmvAttribution.campaign_id.isnot(None),
    ]
    if campaign_id:
        attr_conditions.append(GmvAttribution.campaign_id == campaign_id)
    if start_date:
        attr_conditions.append(GmvAttributionConfirmation.scan_received_at >= start_date)
    if end_date:
        attr_conditions.append(GmvAttributionConfirmation.scan_received_at <= end_date)

    # 按 campaign 分组统计 GMV
    gmv_rows = (
        await db.execute(
            select(
                GmvAttribution.campaign_id,
                func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
                func.count().label("attributed_orders"),
                func.avg(GmvAttribution.confidence_score).label("avg_confidence"),
            )
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(*attr_conditions)
            .group_by(GmvAttribution.campaign_id)
        )
    ).all()

    results = []
    for row in gmv_rows:
        cid = row.campaign_id

        # 查活动信息
        camp = (
            await db.execute(select(Campaign).where(Campaign.id == cid, Campaign.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if not camp:
            continue

        # No authoritative all-eligible campaign visitor cohort exists yet.
        # Converted confirmations cannot be reused as their own denominator.
        scan_count = scan_uv = None
        budget = _extract_budget(camp.rules_json)
        scan_cost = None
        conversion_rate = None
        # 无预算时 ROI 不可计算，返回 None 而不是 0，避免前端把 0x 渲染成异常红
        roi = round(float(row.gmv) / budget, 2) if budget else None

        results.append(
            {
                "campaign_id": str(cid),
                "campaign_name": camp.name,
                "status": camp.status,
                "budget": budget,
                "attributed_gmv": float(row.gmv),
                "attributed_orders": row.attributed_orders,
                "scan_count": scan_count,
                "scan_uv": scan_uv,
                "scan_cost": scan_cost,
                "conversion_rate": conversion_rate,
                "conversion_rate_status": "unavailable_missing_campaign_eligible_cohort",
                "roi": roi,
                "avg_confidence": round(float(row.avg_confidence or 0), 2),
            }
        )

    # 按 GMV 降序
    results.sort(key=lambda x: x["attributed_gmv"], reverse=True)
    return results


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
    conditions = [GmvAttribution.tenant_id == tenant_id, GmvAttribution.authority_status == "confirmed"]
    if match_type:
        conditions.append(GmvAttribution.match_type == match_type)
    if campaign_id:
        conditions.append(GmvAttribution.campaign_id == campaign_id)

    total = (
        await db.execute(
            select(func.count())
            .select_from(GmvAttribution)
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(*conditions)
        )
    ).scalar() or 0

    rows = (
        (
            await db.execute(
                select(GmvAttribution)
                .join(
                    GmvAttributionConfirmation,
                    and_(
                        GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                        GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                    ),
                )
                .where(*conditions)
                .order_by(GmvAttribution.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
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
    scan_count = (
        await db.execute(
            select(func.count())
            .select_from(ScanEvent)
            .where(
                ScanEvent.tenant_id == tenant_id,
                ScanEvent.scan_time >= day_start,
                ScanEvent.scan_time < day_end,
            )
        )
    ).scalar() or 0

    scan_uv = (
        await db.execute(
            select(func.count(func.distinct(ScanEvent.public_id)))
            .select_from(ScanEvent)
            .where(
                ScanEvent.tenant_id == tenant_id,
                ScanEvent.scan_time >= day_start,
                ScanEvent.scan_time < day_end,
            )
        )
    ).scalar() or 0

    # 归因统计（按 channel 维度）
    attr_rows = (
        await db.execute(
            select(
                ExternalOrder.channel,
                func.coalesce(func.sum(GmvAttribution.amount), 0).label("gmv"),
                func.count().label("orders"),
            )
            .join(
                GmvAttribution,
                and_(
                    GmvAttribution.tenant_id == ExternalOrder.tenant_id,
                    GmvAttribution.external_order_id == ExternalOrder.id,
                ),
            )
            .join(
                GmvAttributionConfirmation,
                and_(
                    GmvAttributionConfirmation.tenant_id == GmvAttribution.tenant_id,
                    GmvAttributionConfirmation.attribution_id == GmvAttribution.id,
                ),
            )
            .where(
                GmvAttribution.tenant_id == tenant_id,
                GmvAttribution.authority_status == "confirmed",
                GmvAttributionConfirmation.scan_received_at >= day_start,
                GmvAttributionConfirmation.scan_received_at < day_end,
            )
            .group_by(ExternalOrder.channel)
        )
    ).all()

    upserted = 0
    for row in attr_rows:
        # Upsert using raw SQL for ON CONFLICT
        from sqlalchemy import text

        await db.execute(
            text("""
                INSERT INTO gmv_daily_stats
                    (id, tenant_id, stat_date, channel,
                     attributed_gmv, attributed_orders, scan_count, scan_uv)
                VALUES (gen_random_uuid(), :tid, :dt, :ch, :gmv, :ords, :sc, :suv)
                ON CONFLICT (tenant_id, stat_date, campaign_id, channel)
                DO UPDATE SET attributed_gmv = EXCLUDED.attributed_gmv,
                              attributed_orders = EXCLUDED.attributed_orders,
                              scan_count = EXCLUDED.scan_count,
                              scan_uv = EXCLUDED.scan_uv
            """),
            {
                "tid": str(tenant_id),
                "dt": day_start,
                "ch": row.channel,
                "gmv": float(row.gmv),
                "ords": row.orders,
                "sc": scan_count,
                "suv": scan_uv,
            },
        )
        upserted += 1

    # 如果没有归因数据，也记录扫码统计
    if not attr_rows:
        from sqlalchemy import text

        await db.execute(
            text("""
                INSERT INTO gmv_daily_stats
                    (id, tenant_id, stat_date, campaign_id, channel,
                     attributed_gmv, attributed_orders, scan_count, scan_uv)
                VALUES (gen_random_uuid(), :tid, :dt, NULL, NULL, 0, 0, :sc, :suv)
                ON CONFLICT (tenant_id, stat_date, campaign_id, channel)
                DO UPDATE SET scan_count = EXCLUDED.scan_count,
                              scan_uv = EXCLUDED.scan_uv
            """),
            {"tid": str(tenant_id), "dt": day_start, "sc": scan_count, "suv": scan_uv},
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
