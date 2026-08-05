"""扫码事件服务"""

import uuid

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
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
) -> ScanEvent:
    """记录扫码事件。优先使用 CodeItem.first_scanned_at 原子更新消除首扫竞态条件；
    CodeItem 不存在时回退到查询方式。

    yimatong-zgb1.4 跨租户防御：首查 UPDATE 的 WHERE 加 ``tenant_id`` 过滤，
    防止 control tenant 用错 tenant_id 调用时污染 baseline 的 first_scanned_at
    （public_id 全局 unique 已兜底，应用层显式过滤是 defense-in-depth）。
    """
    # 每一条成功落库的权威扫码事实（包括重复扫码）计一次 max_scans；
    # 无效码、终止状态和写入失败不会产生 ScanEvent，因此不计费。
    await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)

    # 原子判断首扫：更新 first_scanned_at，若之前为空则为首扫。
    # tenant_id 维度过滤：即使 public_id 全局唯一，应用层也显式限定本租户的码，
    # 避免跨租户调用方误写其他租户的首查事实。
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
        # yimatong-zgb1.10：有效访问标记 + 匿名访客关联
        is_valid_visit=is_valid_visit,
        visitor_id=visitor_id,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)

    await event_bus.emit(
        "scan.created",
        {
            "public_id": public_id,
            "is_first_scan": is_first,
            "environment": environment,
            # yimatong-zgb1.7：补 ip_hash（risk_auto_handler 跨区检测需要；之前缺这个字段
            # 导致 cross-region 路径失效）
            "ip_hash": ip_hash,
            "tenant_id": str(tenant_id),
        },
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
