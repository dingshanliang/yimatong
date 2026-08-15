"""扫码事件服务"""

import uuid
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.base import uuid7
from app.models.code import CodeItem
from app.models.scan import ScanEvent
from app.services.quota import check_quota_incremental_locked
from app.utils import utcnow


async def record_scan_event(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    environment: str | None = None,
    visitor_id: str | None = None,
    is_valid_visit: bool = False,
    diversion_observation_owner: Literal["risk_auto_handler", "resolver"] = "risk_auto_handler",
) -> ScanEvent:
    """记录扫码事件；PostgreSQL 由受控函数原子写首扫时间和权威事件。"""
    # 每一条成功落库的权威扫码事实（包括重复扫码）计一次 max_scans；
    # 无效码、终止状态和写入失败不会产生 ScanEvent，因此不计费。
    await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)

    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        event_id = uuid7()
        scan_row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM public.record_public_code_scan("
                        ":tenant_id,:public_id,:event_id,:ip_hash,:user_agent,:environment,:visitor_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "public_id": public_id,
                        "event_id": event_id,
                        "ip_hash": ip_hash,
                        "user_agent": user_agent[:500] if user_agent else None,
                        "environment": environment or "browser",
                        "visitor_id": visitor_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        is_first = bool(scan_row["first_scan"])
        first_scanned_at = scan_row["first_scanned_at"]
        if first_scanned_at is None:
            raise RuntimeError("First-scan authority returned no timestamp")
        event = await db.scalar(select(ScanEvent).where(ScanEvent.tenant_id == tenant_id, ScanEvent.id == event_id))
        if event is None:
            raise RuntimeError("Scan authority returned no event")
    else:
        # SQLite test adapter mirrors the PostgreSQL authority with one
        # tenant-scoped conditional update.
        result = await db.execute(
            sa_update(CodeItem)
            .where(
                CodeItem.public_id == public_id,
                CodeItem.tenant_id == tenant_id,
                CodeItem.first_scanned_at.is_(None),
            )
            .values(first_scanned_at=utcnow())
        )

        if result.rowcount == 1:
            is_first = True
        else:
            # 检查本租户的 CodeItem 是否存在（tenant 维度一致）
            code_exists = await db.execute(
                select(CodeItem.id).where(CodeItem.public_id == public_id, CodeItem.tenant_id == tenant_id)
            )
            if code_exists.scalar_one_or_none():
                # CodeItem 存在但 first_scanned_at 已设置 → 非首查
                is_first = False
            else:
                # CodeItem 不存在（如测试环境直接调用），回退到查询方式
                is_first = await _check_first_scan(db, public_id)
        event = ScanEvent(
            tenant_id=tenant_id,
            public_id=public_id,
            scan_time=utcnow(),
            ip_hash=ip_hash,
            user_agent=user_agent,
            is_first_scan=is_first,
            environment=environment,
            # SQLite test adapter receives the same application classification;
            # PostgreSQL derives validity inside the restricted function.
            is_valid_visit=is_valid_visit,
            visitor_id=visitor_id,
        )
        db.add(event)
        await db.flush()
        await db.refresh(event)

    await event_bus.emit(
        "scan.created",
        {
            "scan_event_id": str(event.id),
            "scan_time": event.scan_time.isoformat(),
            "public_id": public_id,
            "is_first_scan": is_first,
            "environment": environment,
            # yimatong-zgb1.7：补 ip_hash（risk_auto_handler 跨区检测需要；之前缺这个字段
            # 导致 cross-region 路径失效）
            "ip_hash": ip_hash,
            "tenant_id": str(tenant_id),
            # Resolver writes the exact observation in this transaction. Other
            # scan producers retain the event-handler fallback by default.
            "diversion_observation_owner": diversion_observation_owner,
        },
        str(tenant_id),
        db=db,
    )
    return event


async def _check_first_scan(db: AsyncSession, public_id: str) -> bool:
    """检查是否首扫（用于只读场景，不保证并发安全）。"""
    result = await db.execute(select(ScanEvent).where(ScanEvent.public_id == public_id).limit(1))
    return result.scalar_one_or_none() is None


def parse_environment(user_agent: str | None) -> str:
    """从 User-Agent 解析环境"""
    if not user_agent:
        return "browser"
    ua_lower = user_agent.lower()
    if "micromessenger" in ua_lower:
        return "wechat"
    if "alipayclient" in ua_lower or "alipay" in ua_lower:
        return "alipay"
    return "browser"
