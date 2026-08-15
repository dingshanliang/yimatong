"""扫码事件驱动的风控自动评估处理器。

订阅 scan.created 事件，构建评估上下文，调用规则引擎，
命中时触发自动处置动作（冻结码 / 暂停活动 / 发送通知）。
同时执行跨区检测，触发跨区预警规则。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.code import CodeItem, CodeItemStatus
from app.models.risk import RiskRule
from app.models.scan import ScanEvent
from app.utils import utcnow

logger = logging.getLogger(__name__)

def _event_handler_owns_diversion_observation(data: dict) -> bool:
    """Keep fallback ownership unless the producer names resolver authority."""
    return data.get("diversion_observation_owner") != "resolver"


async def _build_scan_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
) -> dict:
    """从 ScanEvent 表构建评估上下文。"""
    now = utcnow()

    # 最近 1 分钟扫码次数
    since_1min = now - timedelta(minutes=1)
    r1 = await db.execute(
        select(func.count())
        .select_from(ScanEvent)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id == public_id,
            ScanEvent.scan_time >= since_1min,
        )
    )
    recent_count = r1.scalar() or 0

    # 最近 10 分钟不同 IP 数
    since_10min = now - timedelta(minutes=10)
    r2 = await db.execute(
        select(func.count(func.distinct(ScanEvent.ip_hash))).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id == public_id,
            ScanEvent.scan_time >= since_10min,
            ScanEvent.ip_hash.isnot(None),
        )
    )
    distinct_ips = r2.scalar() or 0

    # 全部扫码总次数
    r3 = await db.execute(
        select(func.count())
        .select_from(ScanEvent)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id == public_id,
        )
    )
    total_scans = r3.scalar() or 0

    # 当前小时
    current_hour = now.hour

    return {
        "request_count": recent_count,
        "distinct_ips": distinct_ips,
        "total_scans": total_scans,
        "current_hour": current_hour,
    }


async def _build_cross_region_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
) -> dict | None:
    """构建跨区检测上下文：检查码归属区域与扫码实际区域是否匹配。"""
    from app.models.channel import DiversionClue

    # 获取码的归属区域
    from app.services.channel import get_code_expected_region

    expected = await get_code_expected_region(db, tenant_id, public_id)
    if not expected or not (expected.get("coverage_label") or expected.get("city")):
        return None

    # 统计该码最近的跨区事件次数
    window_hours = 24
    since = utcnow() - timedelta(hours=window_hours)
    cross_count_result = await db.execute(
        select(func.count())
        .select_from(DiversionClue)
        .where(
            DiversionClue.tenant_id == tenant_id,
            DiversionClue.public_id == public_id,
            DiversionClue.created_at >= since,
        )
    )
    cross_count = cross_count_result.scalar() or 0

    return {
        "expected_region": expected.get("expected_region") or expected.get("coverage_label") or expected.get("city"),
        "region_name": expected.get("region_name"),
        "store_id": expected.get("store_id"),
        "store_name": expected.get("store_name"),
        "cross_region_count": cross_count,
        "detected_region": None,  # 将在 check_diversion 后填充
    }


async def _check_and_record_diversion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_or_hash: str | None,
    scan_event_id: str | None,
    scan_time: str | None,
) -> dict | None:
    """执行跨区检测并记录线索。返回跨区信息或 None。

    yimatong-zgb1.7：参数改为 ip_or_hash（兼容 raw ip 与 ip_hash）。
    scan.created 事件携带 ip_hash（脱敏），check_diversion 用它做位置推断的 fallback。
    """
    if not ip_or_hash:
        return None

    from app.services.channel import check_diversion

    try:
        clue = await check_diversion(
            db,
            tenant_id,
            public_id,
            ip_or_hash,
            scan_event_id=uuid.UUID(scan_event_id) if scan_event_id else None,
            scan_time=datetime.fromisoformat(scan_time) if scan_time else None,
            observation_ip_hash=ip_or_hash,
        )
        if clue:
            return {
                "detected_city": clue.detected_city,
                "expected_region": clue.expected_region,
                "clue_id": str(clue.id),
            }
    except Exception:
        logger.warning("Cross-region check failed for public_id=%s", public_id, exc_info=True)
    return None


async def _handle_scan_created(event_type: str, data: dict, tenant_id: str) -> None:
    """scan.created 事件处理器：常规风控评估 + 跨区检测。"""
    from app.core.database import async_session_factory, set_session_tenant_context

    public_id = data.get("public_id")
    scan_event_id_raw = data.get("scan_event_id")
    if not public_id or not scan_event_id_raw:
        return

    tenant_uuid = uuid.UUID(tenant_id)
    scan_event_id = uuid.UUID(scan_event_id_raw)

    async with async_session_factory() as db:
        await set_session_tenant_context(db, tenant_uuid)
        try:
            from app.services.entitlement import (
                TenantFeatureDisabledError,
                TenantPlanExpiredError,
                require_active_plan,
                require_tenant_feature,
            )

            try:
                await require_active_plan(db, tenant_uuid)
                await require_tenant_feature(db, tenant_uuid, "risk_module")
            except (TenantFeatureDisabledError, TenantPlanExpiredError):
                return

            # 检查码是否已冻结，冻结码跳过评估
            item_result = await db.execute(
                select(CodeItem).where(
                    CodeItem.public_id == public_id,
                    CodeItem.tenant_id == tenant_uuid,
                )
            )
            code_item = item_result.scalar_one_or_none()
            if code_item and code_item.status == CodeItemStatus.frozen:
                return

            # 查询租户所有启用的风控规则
            rules_result = await db.execute(
                select(RiskRule).where(
                    RiskRule.tenant_id == tenant_uuid,
                    RiskRule.enabled.is_(True),
                )
            )
            rules = list(rules_result.scalars().all())
            if not rules:
                return

            # 构建基础上下文
            context = await _build_scan_context(db, tenant_uuid, public_id)
            context["public_id"] = public_id
            context["code_item_id"] = str(code_item.id) if code_item else None

            # 跨区检测（yimatong-zgb1.7：用 ip_hash 替代 raw ip；scan.created 现在携带 ip_hash）
            ip_hash = data.get("ip_hash")
            diversion_info = None
            if _event_handler_owns_diversion_observation(data):
                diversion_info = await _check_and_record_diversion(
                    db,
                    tenant_uuid,
                    public_id,
                    ip_hash,
                    data.get("scan_event_id"),
                    data.get("scan_time"),
                )
            if diversion_info:
                context["cross_region_detected"] = True
                context["detected_region"] = diversion_info["detected_city"]
                context["expected_region"] = diversion_info["expected_region"]

                # 构建跨区上下文
                cross_ctx = await _build_cross_region_context(db, tenant_uuid, public_id)
                if cross_ctx:
                    cross_ctx["detected_region"] = diversion_info["detected_city"]
                    cross_ctx["cross_region_detected"] = True
                    context.update(cross_ctx)

            # PostgreSQL reloads the exact scan/rule pair and atomically owns
            # evaluation, receipt deduplication, alerting, pauses, and outbox.
            for rule in rules:
                from app.services.risk_action import execute_risk_action

                result = await execute_risk_action(
                    db,
                    tenant_uuid,
                    scan_event_id=scan_event_id,
                    rule=rule,
                    context=context,
                    idempotency_key=f"scan-risk:{scan_event_id}:{rule.id}",
                )
                if result["triggered"]:
                    logger.info(
                        "Risk rule triggered: rule=%s scan_event=%s action=%s replayed=%s",
                        rule.name,
                        scan_event_id,
                        result["action"],
                        result["replayed"],
                    )

            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning("Risk auto-handler transaction rolled back for public_id=%s", public_id)
            raise RuntimeError("Risk auto-handler transaction failed") from None


def init_risk_auto_handler() -> None:
    """应用启动时注册 scan.created 事件处理器。"""
    event_bus.add_handler("scan.created", _handle_scan_created)
    logger.info("Risk auto-handler registered for scan.created events")
