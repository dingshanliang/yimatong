"""认证安全模块单元测试"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

_PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": "postgresql+asyncpg://yimatong_app:runtime-only@db:5432/yimatong",
    "control_database_url": "postgresql+asyncpg://yimatong_control:control-only@db:5432/yimatong",
    "admin_public_url": "https://admin.example.com",
    "platform_public_url": "https://platform.example.com",
    "cookie_secure": True,
    "secret_key": "prod-secret-key-8YQ2jZ6xF4mN9pR7sT5vW3kL1cB0dA",
    "hmac_pepper": "prod-hmac-pepper-1Kx9Qm4Vt7Za2Nc8Wd5Yp3Rf6Bs0Gj",
    "ip_hash_secret": "prod-ip-hash-secret-6Tp2Mz8Qa4Wn9Yc1Rk7Vf5Bj3Hs0Ld",
}


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


@pytest.mark.parametrize(
    "secret_key",
    [
        "dev-secret-key-change-in-production",
        "short-secret",
        "a" * 64,
        "password-password-password-password-password-password",
    ],
)
def test_production_rejects_example_short_or_low_entropy_secret_key(secret_key: str):
    from app.core.config import Settings

    with pytest.raises(ValidationError, match="SECRET_KEY") as exc_info:
        Settings(_env_file=None, **(_PRODUCTION_SETTINGS | {"secret_key": secret_key}))

    assert secret_key not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("hmac_pepper", "", "HMAC_PEPPER"),
        ("hmac_pepper", "ff" * 32, "HMAC_PEPPER"),
        ("hmac_pepper", _PRODUCTION_SETTINGS["secret_key"], "HMAC_PEPPER"),
        ("ip_hash_secret", "yimatong-default-ip-hash-secret-change-in-production", "IP_HASH_SECRET"),
        ("ip_hash_secret", _PRODUCTION_SETTINGS["secret_key"], "IP_HASH_SECRET"),
        ("ip_hash_secret", _PRODUCTION_SETTINGS["hmac_pepper"], "IP_HASH_SECRET"),
    ],
)
def test_production_requires_independent_non_default_application_secrets(field: str, value: str, message: str):
    from app.core.config import Settings

    with pytest.raises(ValidationError, match=message) as exc_info:
        Settings(_env_file=None, **(_PRODUCTION_SETTINGS | {field: value}))

    if value:
        assert value not in str(exc_info.value)


def test_production_accepts_independent_strong_application_secrets():
    from app.core.config import Settings

    configured = Settings(_env_file=None, **_PRODUCTION_SETTINGS)

    assert configured.environment == "production"


def test_development_and_test_keep_local_secret_compatibility():
    from app.core.config import Settings

    for environment in ("development", "test"):
        configured = Settings(
            _env_file=None,
            environment=environment,
            secret_key="local-only",
            hmac_pepper="",
        )
        assert configured.secret_key == "local-only"


@pytest.mark.asyncio
async def test_jwt_auth_loads_permissions_to_request_state():
    """JWT 认证应将账户的权限列表加载到 request.state.permissions"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    # Mock DB 返回带权限的账户
    mock_perm = MagicMock()
    mock_perm.code = "product:create"

    mock_role = MagicMock()
    mock_role.name = "admin"
    mock_role.permissions = [mock_perm]

    mock_account = MagicMock()
    mock_account.roles = [mock_role]
    mock_account.is_active = True
    mock_account.auth_version = 0

    mock_result = MagicMock()
    from app.models.tenant import TenantStatus

    mock_result.one_or_none.return_value = (mock_account, TenantStatus.active)

    mock_db = AsyncMock()
    # _load_account_access 现在用 set_session_tenant_context（一条 set_config 执行）
    # 建立租户上下文，再执行账户/租户查询。让 _session_uses_postgresql 返回 True，
    # 否则会跳过 set_config，side_effect 数量就对不上。
    mock_db.execute.side_effect = [MagicMock(), mock_result]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    tenant_id = str(uuid.uuid4())
    with (
        patch("app.core.database._is_pg", True),
        patch("app.core.database._session_uses_postgresql", return_value=True),
        patch("app.core.database.async_session_factory", return_value=mock_session),
    ):
        has_access, permissions = await middleware._load_account_access(str(uuid.uuid4()), "admin", 0, tenant_id)

    assert has_access is True
    assert "product:create" in permissions


@pytest.mark.asyncio
async def test_load_permissions_fails_closed_on_database_error():
    """权限加载失败时普通租户应 fail-closed"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    with (
        patch("app.core.database._is_pg", True),
        patch("app.core.database.async_session_factory", side_effect=Exception("DB error")),
    ):
        has_access, permissions = await middleware._load_account_access(
            str(uuid.uuid4()), "admin", 0, str(uuid.uuid4())
        )

    assert has_access is False
    assert permissions == []


@pytest.mark.asyncio
async def test_load_permissions_fails_closed_for_missing_account():
    """找不到普通租户账户时不得回退到代码角色权限"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    mock_result = MagicMock()
    mock_result.one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.side_effect = [MagicMock(), mock_result]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.core.database._is_pg", True),
        patch("app.core.database.async_session_factory", return_value=mock_session),
    ):
        has_access, permissions = await middleware._load_account_access(
            str(uuid.uuid4()), "admin", 0, str(uuid.uuid4())
        )

    assert has_access is False
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


@pytest.mark.asyncio
async def test_logout_persists_refresh_token_when_access_token_is_invalid(db):
    import uuid

    from app.models.auth_security import AuthSession
    from app.models.tenant import Account, Organization, Tenant
    from app.services.auth import logout_session
    from app.utils.security import create_refresh_token

    tenant = Tenant(id=uuid.uuid4(), name="Logout test", slug=f"logout-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(id=uuid.uuid4(), tenant_id=tenant.id, name="Logout test")
    db.add(organization)
    await db.flush()
    account = Account(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        organization_id=organization.id,
        email=f"logout-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="unused",
        name="Logout test",
    )
    db.add(account)
    await db.commit()

    session_id = uuid.uuid4()
    refresh_token = create_refresh_token(
        str(account.id),
        extra={
            "sid": str(session_id),
            "tenant_id": str(tenant.id),
            "auth_version": account.auth_version,
        },
    )
    cache = AsyncMock()

    await logout_session(
        db=db,
        access_token="invalid-access-token",
        refresh_token_str=refresh_token,
        cache=cache,
    )

    auth_session = await db.get(AuthSession, session_id)
    assert auth_session is not None
    assert auth_session.revoked_at is not None
    cache.revoke_token.assert_awaited_once()
