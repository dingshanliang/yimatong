"""认证安全模块单元测试"""

import os
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_config_rejects_empty_secret_key():
    """空 secret_key 应在启动时拒绝"""
    from pydantic import ValidationError
    from app.core.config import Settings

    original = os.environ.get("SECRET_KEY")
    os.environ["SECRET_KEY"] = ""
    try:
        with pytest.raises((ValidationError, ValueError)):
            Settings()
    finally:
        if original:
            os.environ["SECRET_KEY"] = original
        else:
            os.environ.pop("SECRET_KEY", None)


@pytest.mark.asyncio
async def test_jwt_auth_loads_permissions_to_request_state():
    """JWT 认证应将账户的权限列表加载到 request.state.permissions"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    # Mock DB 返回带权限的账户
    mock_perm = MagicMock()
    mock_perm.code = "product:create"

    mock_role = MagicMock()
    mock_role.permissions = [mock_perm]

    mock_account = MagicMock()
    mock_account.roles = [mock_role]

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_account

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.database.async_session_factory", return_value=mock_session):
        permissions = await middleware._load_permissions(str(uuid.uuid4()), "admin")

    assert "product:create" in permissions


@pytest.mark.asyncio
async def test_load_permissions_returns_empty_on_failure():
    """权限加载失败应降级为空列表"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    with patch("app.core.database.async_session_factory", side_effect=Exception("DB error")):
        permissions = await middleware._load_permissions("some-id", "admin")

    assert permissions == []


@pytest.mark.asyncio
async def test_load_permissions_returns_empty_for_no_account():
    """找不到账户应返回空列表"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.database.async_session_factory", return_value=mock_session):
        permissions = await middleware._load_permissions("nonexistent-id", "admin")

    assert permissions == []


@pytest.mark.asyncio
async def test_verify_refresh_token_rejects_blacklisted():
    """已撤销的 refresh token 应被拒绝"""
    from app.utils.security import create_refresh_token, decode_token, verify_refresh_token

    token = create_refresh_token("test-account-id")

    # 未撤销时应该能解码
    payload = decode_token(token)
    assert payload is not None
    assert payload.get("type") == "refresh"

    # 模拟 token 已被撤销（AsyncRedisCache 在函数内延迟导入，需 patch 源模块）
    with patch("app.services.redis_cache.AsyncRedisCache") as mock_cache_cls:
        mock_cache = AsyncMock()
        mock_cache.is_token_revoked.return_value = True
        mock_cache_cls.return_value = mock_cache
        result = await verify_refresh_token(token)
        assert result is None  # 应被拒绝


@pytest.mark.asyncio
async def test_verify_refresh_token_accepts_valid():
    """未撤销的 refresh token 应被接受"""
    from app.utils.security import create_refresh_token, verify_refresh_token

    token = create_refresh_token("test-account-id")

    with patch("app.services.redis_cache.AsyncRedisCache") as mock_cache_cls:
        mock_cache = AsyncMock()
        mock_cache.is_token_revoked.return_value = False
        mock_cache_cls.return_value = mock_cache
        result = await verify_refresh_token(token)
        assert result is not None
        assert result.get("type") == "refresh"
