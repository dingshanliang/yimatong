import uuid

import pytest
from starlette.applications import Starlette

from app.middleware.tenant import TenantScopeMiddleware, _api_key_metadata_is_invalid


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
    assert middleware._acting_path_is_explicitly_supported("/api/v1/connectors/connectors", ["campaigns"], "GET")
    assert middleware._acting_path_is_explicitly_supported(
        "/api/v1/connectors/connectors/00000000-0000-0000-0000-000000000001/sync-stock",
        ["campaigns"],
        "POST",
    )
    assert not middleware._acting_path_is_explicitly_supported(
        "/api/v1/connectors/connectors",
        ["analytics"],
        "GET",
    )
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/analytics/dashboard", ["products"])
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/tenants/me", ["products", "pages"])


def test_import_routes_have_exact_method_bound_agency_scope_mapping():
    middleware = TenantScopeMiddleware(Starlette())

    assert middleware._acting_path_is_explicitly_supported("/api/v1/imports/excel", ["products"], "POST")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/imports/products", ["products"], "POST")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/imports/existing-codes", ["codes"], "POST")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/imports/template", ["products"], "GET")
    assert middleware._acting_path_is_explicitly_supported("/api/v1/imports/records", ["products"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/template", ["codes"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/records", ["codes"], "GET")

    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/excel", ["products"], method)
        assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/products", ["products"], method)
        assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/existing-codes", ["codes"], method)

    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/existing-codes", ["products"], "POST")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/products", ["codes"], "POST")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/template", ["products"], "POST")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/template/extra", ["products"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports/excel/preview", ["products"], "POST")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/imports", ["products", "codes"], "POST")


def test_risk_alert_routes_have_exact_codes_scope_mapping():
    middleware = TenantScopeMiddleware(Starlette())
    alert_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    assert middleware._acting_path_is_explicitly_supported("/api/v1/risk-alerts", ["codes"], "GET")
    assert middleware._acting_path_is_explicitly_supported(f"/api/v1/risk-alerts/{alert_id}/resolve", ["codes"], "POST")
    assert middleware._acting_path_is_explicitly_supported(
        f"/api/v1/risk-alerts/code-items/{item_id}/freeze", ["codes"], "POST"
    )
    assert middleware._acting_path_is_explicitly_supported(
        f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze", ["codes"], "POST"
    )

    assert not middleware._acting_path_is_explicitly_supported("/api/v1/risk-alerts", ["analytics"], "GET")
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/risk-alerts", ["codes"], "POST")
    assert not middleware._acting_path_is_explicitly_supported(
        f"/api/v1/risk-alerts/{alert_id}/resolve", ["codes"], "GET"
    )
    assert not middleware._acting_path_is_explicitly_supported(
        f"/api/v1/risk-alerts/code-items/{item_id}/freeze", ["codes"], "GET"
    )
    assert not middleware._acting_path_is_explicitly_supported(
        f"/api/v1/risk-alerts/code-items/{item_id}/void", ["codes"], "POST"
    )
    assert not middleware._acting_path_is_explicitly_supported("/api/v1/risk-alerts/unrelated", ["codes"], "GET")


def test_agency_without_acting_context_cannot_write_product_materials():
    middleware = TenantScopeMiddleware(Starlette())

    assert middleware._is_brand_write_surface("/api/v1/product-assets/123", "PATCH")
    assert middleware._is_brand_write_surface("/api/v1/files/upload", "POST")
    assert not middleware._is_brand_write_surface("/api/v1/files/upload", "GET")
    assert middleware._is_brand_write_surface("/api/v1/imports/excel", "POST")
    assert middleware._is_brand_write_surface("/api/v1/imports/products", "POST")
    assert middleware._is_brand_write_surface("/api/v1/imports/existing-codes", "POST")
    assert not middleware._is_brand_write_surface("/api/v1/imports/products", "GET")
    assert not middleware._is_brand_write_surface("/api/v1/imports/products/preview", "POST")


def test_agency_without_acting_context_cannot_mutate_risk_alerts_or_codes():
    middleware = TenantScopeMiddleware(Starlette())
    alert_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    assert middleware._is_brand_write_surface(f"/api/v1/risk-alerts/{alert_id}/resolve", "POST")
    assert middleware._is_brand_write_surface(f"/api/v1/risk-alerts/code-items/{item_id}/freeze", "POST")
    assert middleware._is_brand_write_surface(f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze", "POST")
    assert not middleware._is_brand_write_surface("/api/v1/risk-alerts", "GET")
    assert middleware._requires_client_workspace("/api/v1/risk-alerts", "GET")
    assert middleware._requires_client_workspace(f"/api/v1/risk-alerts/{alert_id}/resolve", "POST")
    assert not middleware._requires_client_workspace("/api/v1/agency/projects", "GET")


class _DatabaseError(Exception):
    def __init__(self, sqlstate: str):
        self.orig = type("Original", (), {"sqlstate": sqlstate})()


def test_api_key_catalog_drift_maps_only_invalid_metadata_to_auth_failure():
    assert _api_key_metadata_is_invalid(_DatabaseError("22023")) is True
    assert _api_key_metadata_is_invalid(_DatabaseError("42501")) is False
    assert _api_key_metadata_is_invalid(RuntimeError("database unavailable")) is False
