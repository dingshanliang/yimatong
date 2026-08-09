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


@pytest.mark.anyio
async def test_postgres_auth_session_loading_failure_is_fail_closed(monkeypatch):
    monkeypatch.setattr("app.core.database._is_pg", True)
    monkeypatch.setattr("app.core.database.control_session_factory", lambda: FailingSessionContext())
    middleware = TenantScopeMiddleware(Starlette())

    has_access = await middleware._load_auth_session_access(
        session_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        token_auth_version=0,
    )

    assert has_access is False


def test_acting_context_routes_are_mapped_to_live_scope():
    middleware = TenantScopeMiddleware(Starlette())

    assert middleware._acting_path_is_explicitly_supported("/api/v1/products", ["products"])
    assert middleware._acting_path_is_explicitly_supported("/api/v1/product-assets/123", ["products"], "PATCH")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/product-assets/123", ["products"], "DELETE")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/product-assets/123", ["products"], "GET")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/files/upload", ["products"], "POST")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/files/upload", ["products"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/files/123", ["products"], "GET")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me/categories", ["products"], "GET")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me/entitlement", ["campaigns"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me/entitlement", ["campaigns"], "POST")
    assert not middleware._acting_path_is_explicitly_supported(
        "/api/v1/tenants/me/entitlement/details", ["campaigns"], "GET"
    )
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me/categories", ["products"], "POST")
    assert not middleware._acting_path_is_explicitly_supported(
        "/api/v1/tenants/me/categories/custom", ["products"], "GET"
    )
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/product-assets/123", ["pages"], "PATCH")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/page-versions/123", ["pages"])
    assert middleware._acting_path_is_explicitly_supported("/api/v1/products", ["pages"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/products", ["pages"], "POST")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/integrations/wecom", ["campaigns"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/analytics/dashboard", ["products"])
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me", ["products", "pages"])


def test_agency_without_acting_context_cannot_write_product_materials():
    middleware = TenantScopeMiddleware(Starlette())

    assert middleware._is_brand_write_surface("/api/v1/product-assets/123", "PATCH")
    assert middleware._is_brand_write_surface("/api/v1/files/upload", "POST")
    assert not middleware._is_brand_write_surface("/api/v1/files/upload", "GET")
