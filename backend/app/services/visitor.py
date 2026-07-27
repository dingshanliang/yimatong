"""匿名访客服务（yimatong-zgb1.10 增长转化线主干）。

Decision 22：匿名访客接收 first-party 匿名访客标识。身份可通过权威关系升级。
本服务负责签发、解析、关联匿名访客。

设计：
- 签发：首次访问（无 X-Visitor-ID 头或 ID 不存在）时，创建 AnonymousVisitor 行，
  返回 visitor_id。H5 存 localStorage，后续请求携带。
- 解析：请求带 X-Visitor-ID 时，验证存在性；不存在则签发新的（防御伪造）。
- 关联：visitor_id 跨会话稳定，清缓存即换新（保守，不做弱信号合并）。
- 不存 PII，只存聚合分析信号。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visitor import AnonymousVisitor
from app.utils import utcnow


async def resolve_or_create_visitor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    visitor_id: str | None,
    environment: str | None = None,
    ip_hash: str | None = None,
) -> AnonymousVisitor:
    """解析或创建匿名访客。

    - 若 visitor_id 提供且存在 → 更新 last_seen_at，返回已有访客。
    - 若 visitor_id 缺失或不存在 → 创建新访客（签发新 visitor_id）。

    返回 AnonymousVisitor 实例（已 flush，caller 负责 commit）。
    """
    if visitor_id:
        existing = await db.execute(
            select(AnonymousVisitor).where(
                AnonymousVisitor.visitor_id == visitor_id,
                AnonymousVisitor.tenant_id == tenant_id,
            )
        )
        visitor = existing.scalar_one_or_none()
        if visitor:
            visitor.last_seen_at = utcnow()
            await db.flush()
            return visitor

    # 签发新 visitor_id
    new_visitor_id = uuid.uuid4().hex
    visitor = AnonymousVisitor(
        tenant_id=tenant_id,
        visitor_id=new_visitor_id,
        first_environment=environment,
        first_ip_hash=ip_hash,
    )
    db.add(visitor)
    await db.flush()
    return visitor


async def link_visitor_to_consumer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    visitor_id: str,
    consumer_id: uuid.UUID,
) -> bool:
    """身份升级：将匿名访客关联到 ConsumerProfile（Decision 22 升级路径）。

    留资/绑定后调用。返回是否成功关联。
    """
    result = await db.execute(
        select(AnonymousVisitor).where(
            AnonymousVisitor.visitor_id == visitor_id,
            AnonymousVisitor.tenant_id == tenant_id,
        )
    )
    visitor = result.scalar_one_or_none()
    if not visitor:
        return False
    visitor.consumer_id = consumer_id
    await db.flush()
    return True
