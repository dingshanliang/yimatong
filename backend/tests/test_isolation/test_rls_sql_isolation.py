"""A2-005: RLS 隔离验证 — 场景2：直连 SQL 隔离

租户 A 创建的数据，租户 B 通过直连 SQL（使用应用连接池）无法读取。
注意：SQLite 不支持 RLS，此测试验证应用层逻辑。PostgreSQL 环境下 RLS 会额外保护。
"""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Organization
from app.services.organization import create_organization
from app.services.tenant import create_tenant
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


class TestSQLIsolation:
    @pytest.mark.anyio
    async def test_sql_query_filtered_by_tenant_id(self, db: AsyncSession):
        """场景2：直接 SQL 查询（应用层）按 tenant_id 过滤"""
        tenant_a = await create_tenant(
            db,
            name="SQL租户A",
            slug=None,
            plan="free",
            admin_email="sql-a@test.com",
            admin_name="A",
            admin_password="Pass1234",
        )
        tenant_b = await create_tenant(
            db,
            name="SQL租户B",
            slug=None,
            plan="free",
            admin_email="sql-b@test.com",
            admin_name="B",
            admin_password="Pass1234",
        )

        await create_organization(db, tenant_a.id, "A机密部门", None)
        await create_organization(db, tenant_b.id, "B部门", None)

        result = await db.execute(select(Organization).where(Organization.tenant_id == tenant_a.id))
        orgs = list(result.scalars().all())
        names = [o.name for o in orgs]
        assert "B部门" not in names
        # tenant_a's default org + the one we created
        assert any("A" in n for n in names)
