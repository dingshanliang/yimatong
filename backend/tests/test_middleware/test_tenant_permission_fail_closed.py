import uuid

import pytest
from starlette.applications import Starlette

from app.middleware.tenant import TenantScopeMiddleware


class FailingSessionContext:
    async def __aenter__(self):
        raise RuntimeError("database unavailable")

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.anyio
async def test_postgres_permission_loading_failure_is_fail_closed(monkeypatch):
    monkeypatch.setattr("app.core.database._is_pg", True)
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: FailingSessionContext())
    middleware = TenantScopeMiddleware(Starlette())

    has_access, permissions = await middleware._load_account_access(
        account_id=str(uuid.uuid4()),
        role="admin",
        token_auth_version=0,
        tenant_id=str(uuid.uuid4()),
    )

    assert has_access is False
    assert permissions == []


@pytest.mark.anyio
async def test_platform_role_only_bypasses_account_lookup_in_platform_boundary(monkeypatch):
    monkeypatch.setattr("app.core.database._is_pg", True)
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: FailingSessionContext())
    middleware = TenantScopeMiddleware(Starlette())

    platform_access, platform_permissions = await middleware._load_account_access(
        account_id="platform-admin",
        role="platform_admin",
        token_auth_version=0,
        tenant_id="platform",
    )
    tenant_access, tenant_permissions = await middleware._load_account_access(
        account_id=str(uuid.uuid4()),
        role="platform_admin",
        token_auth_version=0,
        tenant_id=str(uuid.uuid4()),
    )

    assert platform_access is True
    assert "platform:admin" in platform_permissions
    assert tenant_access is False
    assert tenant_permissions == []
