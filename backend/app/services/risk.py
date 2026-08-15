"""风险预警检测服务"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.event_bus import event_bus
from app.core.exceptions import ConflictError
from app.models.code import CodeItem, CodeItemStatus
from app.models.risk import RiskAlert, RiskAlertType
from app.models.scan import ScanEvent
from app.utils import utcnow

# 多地扫码检测阈值：同一码在 10 分钟内从 2+ 不同 IP 扫描
MULTI_LOCATION_WINDOW_MINUTES = 10
MULTI_LOCATION_IP_THRESHOLD = 2

# 疑似复制码检测阈值：同一码在 1 分钟内被扫描超过 10 次
SUSPECTED_COPY_WINDOW_MINUTES = 1
SUSPECTED_COPY_SCAN_THRESHOLD = 10


async def check_multi_location(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_hash: str,
    code_item_id: uuid.UUID | None = None,
) -> RiskAlert | None:
    """检测同一码短时间内从不同 IP 扫描"""
    since = utcnow() - timedelta(minutes=MULTI_LOCATION_WINDOW_MINUTES)
    result = await db.execute(
        select(func.count(func.distinct(ScanEvent.ip_hash))).where(
            ScanEvent.public_id == public_id,
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= since,
            ScanEvent.ip_hash.isnot(None),
        )
    )
    distinct_ips = result.scalar() or 0

    if distinct_ips >= MULTI_LOCATION_IP_THRESHOLD:
        if code_item_id is None:
            code_result = await db.execute(
                select(CodeItem).where(
                    CodeItem.tenant_id == tenant_id,
                    CodeItem.public_id == public_id,
                )
            )
            item = code_result.scalar_one_or_none()
            if item is None:
                return None
            code_item_id = item.id

        alert = RiskAlert(
            tenant_id=tenant_id,
            alert_type=RiskAlertType.multi_location,
            public_id=public_id,
            code_item_id=code_item_id,
            detail=f"同一码在{MULTI_LOCATION_WINDOW_MINUTES}分钟内从{distinct_ips}个不同IP扫描",
            ip_hash=ip_hash,
        )
        db.add(alert)
        await db.flush()
        await event_bus.emit(
            "risk.alert",
            {"alert_type": "multi_location", "public_id": public_id, "detail": alert.detail},
            str(tenant_id),
            db=db,
        )
        return alert
    return None


async def check_suspected_copy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_hash: str,
    code_item_id: uuid.UUID | None = None,
) -> RiskAlert | None:
    """检测同一码短时间内高频扫码（疑似复制码）"""
    since = utcnow() - timedelta(minutes=SUSPECTED_COPY_WINDOW_MINUTES)
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
        if code_item_id is None:
            code_result = await db.execute(
                select(CodeItem).where(
                    CodeItem.tenant_id == tenant_id,
                    CodeItem.public_id == public_id,
                )
            )
            item = code_result.scalar_one_or_none()
            if item is None:
                return None
            code_item_id = item.id

        alert = RiskAlert(
            tenant_id=tenant_id,
            alert_type=RiskAlertType.suspected_copy,
            public_id=public_id,
            code_item_id=code_item_id,
            detail=f"同一码在{SUSPECTED_COPY_WINDOW_MINUTES}分钟内被扫描{scan_count}次",
            ip_hash=ip_hash,
        )
        db.add(alert)
        await db.flush()
        await event_bus.emit(
            "risk.alert",
            {"alert_type": "suspected_copy", "public_id": public_id, "detail": alert.detail},
            str(tenant_id),
            db=db,
        )
        return alert
    return None


async def freeze_code_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    actor_id: str,
    reason: str,
    idempotency_key: str | None = None,
) -> CodeItem:
    """冻结码项"""
    normalized_reason = reason.strip()
    if not normalized_reason or len(normalized_reason) > 200:
        raise ConflictError("Code lifecycle reason is invalid", error_code="CODE_LIFECYCLE_CONFLICT")

    from app.core.database import _session_uses_postgresql
    from app.services.code import _invalidate_resolve_cache, _lifecycle_auth_session_id

    if _session_uses_postgresql(db):
        normalized_idempotency_key = (idempotency_key or f"manual-freeze:{uuid7()}").strip()
        if not normalized_idempotency_key or len(normalized_idempotency_key) > 128:
            raise ConflictError("Code lifecycle idempotency key is invalid", error_code="CODE_LIFECYCLE_CONFLICT")
        await db.execute(
            text(
                "SELECT * FROM public.freeze_code_item_with_risk_alert("
                ":tenant_id,:auth_session_id,:receipt_id,:audit_id,:alert_id,:item_id,:idempotency_key,:reason)"
            ),
            {
                "tenant_id": tenant_id,
                "auth_session_id": _lifecycle_auth_session_id(),
                "receipt_id": uuid7(),
                "audit_id": uuid7(),
                "alert_id": uuid7(),
                "item_id": item_id,
                "idempotency_key": normalized_idempotency_key,
                "reason": normalized_reason,
            },
        )
        item = await db.scalar(
            select(CodeItem)
            .where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Code item not found")
        await _invalidate_resolve_cache(item.public_id)
        return item

    from app.services.code_state import can_transition

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")

    previous_status = item.status
    can_transition(previous_status, CodeItemStatus.frozen, raise_on_invalid=True)
    item.status = CodeItemStatus.frozen
    item.frozen_from_status = previous_status.value
    item.frozen_at = utcnow()
    item.frozen_by = actor_id
    item.freeze_reason = normalized_reason
    item.freeze_provenance_version = 1

    # 记录冻结预警
    alert = RiskAlert(
        tenant_id=tenant_id,
        alert_type=RiskAlertType.risk_frozen,
        public_id=item.public_id,
        code_item_id=item.id,
        detail="码已被风险冻结",
    )
    db.add(alert)
    await db.flush()
    # 状态变更审计（yimatong-zgb1.3 AC5）
    from app.services.code import _audit_code_op

    await _audit_code_op(
        db,
        actor_id,
        str(tenant_id),
        "code_freeze",
        f"code_item:{item.public_id}",
        details={
            "reason": normalized_reason,
            "before": {"status": previous_status.value},
            "after": {"status": CodeItemStatus.frozen.value},
        },
    )
    await db.refresh(item)
    return item


async def unfreeze_code_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    actor_id: str,
) -> CodeItem:
    """解冻码项，恢复为 activated 状态"""
    from app.services.code import transition_code_item_lifecycle

    controlled = await transition_code_item_lifecycle(db, tenant_id, item_id, "recover", None)
    if controlled is not None:
        item = await db.scalar(
            select(CodeItem)
            .where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Code item not found")
        return item

    result = await db.execute(select(CodeItem).where(CodeItem.id == item_id, CodeItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Code item not found")

    if item.status != CodeItemStatus.frozen:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Code item is not frozen")

    if item.freeze_provenance_version != 1 or item.frozen_from_status not in {
        CodeItemStatus.activated.value,
        CodeItemStatus.bound.value,
    }:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Code item is not recoverable")
    previous_status = item.status
    recovered_status = CodeItemStatus(item.frozen_from_status)
    item.status = recovered_status
    item.frozen_from_status = None
    item.frozen_at = None
    item.frozen_by = None
    item.freeze_reason = None
    item.freeze_provenance_version = None
    await db.flush()
    # 状态变更审计（yimatong-zgb1.3 AC5）
    from app.services.code import _audit_code_op

    await _audit_code_op(
        db,
        actor_id,
        str(tenant_id),
        "code_recover",
        f"code_item:{item.public_id}",
        details={
            "reason": None,
            "before": {"status": previous_status.value},
            "after": {"status": recovered_status.value},
        },
    )
    await db.refresh(item)
    return item


async def resolve_risk_alert(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    alert_id: uuid.UUID,
    actor_id: str,
) -> RiskAlert:
    """Mark a tenant-owned alert resolved and append its actor-bound audit atomically."""
    from fastapi import HTTPException

    alert = await db.scalar(
        select(RiskAlert).where(
            RiskAlert.id == alert_id,
            RiskAlert.tenant_id == tenant_id,
        )
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Risk alert not found")

    was_resolved = alert.resolved
    alert.resolved = True
    await db.flush()

    from app.services.code import _audit_code_op

    await _audit_code_op(
        db,
        actor_id,
        str(tenant_id),
        "risk_alert_resolved",
        f"risk_alert:{alert.id}",
        details={
            "public_id": alert.public_id,
            "before": {"resolved": was_resolved},
            "after": {"resolved": True},
        },
    )
    await db.refresh(alert)
    return alert


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
