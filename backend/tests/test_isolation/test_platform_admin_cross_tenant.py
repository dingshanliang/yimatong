"""A2-005: RLS 隔离验证 — 场景4：平台管理员跨租户读取

平台管理员（superuser）可跨租户读取数据。
此测试验证平台管理员角色标记存在，完整跨租户功能将在 A2-006 实现。
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestPlatformAdminCrossTenant:
    @pytest.mark.anyio
    async def test_platform_admin_token_has_marker(self, client: AsyncClient):
        """场景4（部分）：平台管理员 token 应包含 is_platform_admin 标记"""
        from app.utils.security import decode_token

        # 验证当前 token 结构支持 role 字段
        token = create_access_token("platform", "admin-001", "platform_admin")
        payload = decode_token(token)
        assert payload["role"] == "platform_admin"
        assert payload["tenant_id"] == "platform"

    @pytest.mark.anyio
    async def test_platform_admin_role_exists_in_rbac(self):
        """场景4（部分）：RBAC 系统中存在 platform_admin 角色"""
        from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS

        assert "platform_admin" in WEB_ROLE_PERMISSIONS
