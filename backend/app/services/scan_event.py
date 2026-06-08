"""扫码事件服务"""

import uuid

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.code import CodeItem
from app.models.scan import ScanEvent
from app.utils import utcnow


async def record_scan_event(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    environment: str | None = None,
) -> ScanEvent:
    """记录扫码事件。优先使用 CodeItem.first_scanned_at 原子更新消除首扫竞态条件；
    CodeItem 不存在时回退到查询方式。"""
    # 原子判断首扫：更新 first_scanned_at，若之前为空则为首扫
    result = await db.execute(
        sa_update(CodeItem)
        .where(CodeItem.public_id == public_id, CodeItem.first_scanned_at.is_(None))
        .values(first_scanned_at=utcnow())
    )

    if result.rowcount == 1:
        is_first = True
    else:
        # 检查 CodeItem 是否存在
        code_exists = await db.execute(
            select(CodeItem.id).where(CodeItem.public_id == public_id)
        )
        if code_exists.scalar_one_or_none():
            # CodeItem 存在但 first_scanned_at 已设置
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
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)

    await event_bus.emit(
        "scan.created",
        {"public_id": public_id, "is_first_scan": is_first, "environment": environment},
        str(tenant_id),
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
