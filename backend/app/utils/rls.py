from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def set_tenant_context(conn: AsyncConnection, tenant_id: str) -> None:
    """设置事务级 tenant_id，用于 RLS 策略过滤"""
    await conn.execute(text("SET LOCAL app.tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})


async def set_bypass_rls(conn: AsyncConnection) -> None:
    """显式启用 RLS 绕过（平台管理员/后台任务专用）"""
    await conn.execute(text("SET LOCAL app.bypass_rls = 'true'"))
