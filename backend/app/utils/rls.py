from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def set_tenant_context(conn: AsyncConnection, tenant_id: str) -> None:
    """设置事务级 tenant_id，用于 RLS 策略过滤"""
    await conn.execute(text("SET LOCAL app.tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
