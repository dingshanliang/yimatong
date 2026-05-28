"""风险预警检测服务"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeItem, CodeItemStatus
from app.models.risk import RiskAlert, RiskAlertType
from app.models.scan import ScanEvent

# 多地扫码检测阈值：同一码在 10 分钟内从 2+ 不同 IP 扫描
MULTI_LOCATION_WINDOW_MINUTES = 10
MULTI_LOCATION_IP_THRESHOLD = 2

# 疑似复制码检测阈值：同一码在 1 分钟内被扫描超过 10 次
SUSPECTED_COPY_WINDOW_MINUTES = 1
SUSPECTED_COPY_SCAN_THRESHOLD = 10


async def check_multi_location(
    db: AsyncSession, tenant_id: uuid.UUID, public_id: str, ip_hash: str,
) -> RiskAlert | None:
    """检测同一码短时间内从不同 IP 扫描"""
    since = datetime.now(UTC) - timedelta(minutes=MULTI_LOCATION_WINDOW_MINUTES)
    result = await db.execute(
        select(func.count(func.distinct(ScanEvent.ip_hash)))
        .where(
            ScanEvent.public_id == public_id,
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= since,
            ScanEvent.ip_hash.isnot(None),
        )
    )
    distinct_ips = result.scalar() or 0

    if distinct_ips >= MULTI_LOCATION_IP_THRESHOLD:
        code_result = await db.execute(
            select(CodeItem).where(CodeItem.public_id == public_id)
        )
        item = code_result.scalar_one_or_none()

        alert = RiskAlert(
            tenant_id=tenant_id,
            alert_type=RiskAlertType.multi_location,
            public_id=public_id,
            code_item_id=item.id if item else uuid.uuid4(),
            detail=f"同一码在{MULTI_LOCATION_WINDOW_MINUTES}分钟内从{distinct_ips}个不同IP扫描",
            ip_hash=ip_hash,
        )
        db.add(alert)
        await db.flush()
        return alert
    return None


async def check_suspected_copy(
    db: AsyncSession, tenant_id: uuid.UUID, public_id: str, ip_hash: str,
) -> RiskAlert | None:
    """检测同一码短时间内高频扫码（疑似复制码）"""
    since = datetime.now(UTC) - timedelta(minutes=SUSPECTED_COPY_WINDOW_MINUTES)
    result = await db.execute(
        select(func.count())
        .select_from(ScanEvent)
        .where(
            ScanEvent.public_id == public_id,
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= since,
        )
    )
    scan_count = result.scalar() or 0

    if scan_count >= SUSPECTED_COPY_SCAN_THRESHOLD:
        code_result = await db.execute(
            select(CodeItem).where(CodeItem.public_id == public_id)
        )
        item = code_result.scalar_one_or_none()

        alert = RiskAlert(
            tenant_id=tenant_id,
            alert_type=RiskAlertType.suspected_copy,
            public_id=public_id,
            code_item_id=item.id if item else uuid.uuid4(),
            detail=f"同一码在{SUSPECTED_COPY_WINDOW_MINUTES}分钟内被扫描{scan_count}次",
            ip_hash=ip_hash,
        )
        db.add(alert)
        await db.flush()
        return alert
    return None


async def freeze_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID,
) -> CodeItem:
    """冻结码项"""
    from app.services.code_state import can_transition

    result = await db.execute(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Code item not found")

    can_transition(item.status, CodeItemStatus.frozen, raise_on_invalid=True)
    item.status = CodeItemStatus.frozen

    # 记录冻结预警
    alert = RiskAlert(
        tenant_id=tenant_id,
        alert_type=RiskAlertType.risk_frozen,
        public_id=item.public_id,
        code_item_id=item.id,
        detail="码已被风险冻结",
    )
    db.add(alert)
    await db.commit()
    await db.refresh(item)
    return item


async def unfreeze_code_item(
    db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID,
) -> CodeItem:
    """解冻码项，恢复为 activated 状态"""
    result = await db.execute(
        select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Code item not found")

    if item.status != CodeItemStatus.frozen:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Code item is not frozen")

    item.status = CodeItemStatus.activated
    await db.commit()
    await db.refresh(item)
    return item


async def list_risk_alerts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    alert_type: str | None = None,
    resolved: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RiskAlert], int]:
    """查询风险预警列表"""
    stmt = select(RiskAlert).where(RiskAlert.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(RiskAlert).where(RiskAlert.tenant_id == tenant_id)

    if alert_type:
        stmt = stmt.where(RiskAlert.alert_type == alert_type)
        count_stmt = count_stmt.where(RiskAlert.alert_type == alert_type)
    if resolved is not None:
        stmt = stmt.where(RiskAlert.resolved == resolved)
        count_stmt = count_stmt.where(RiskAlert.resolved == resolved)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(RiskAlert.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
