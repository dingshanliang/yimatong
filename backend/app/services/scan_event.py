"""扫码事件服务"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    """记录扫码事件"""
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
    await db.commit()
    await db.refresh(event)
    return event


async def _check_first_scan(db: AsyncSession, public_id: str) -> bool:
    """检查是否首扫"""
    result = await db.execute(
        select(ScanEvent).where(ScanEvent.public_id == public_id).limit(1)
    )
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
