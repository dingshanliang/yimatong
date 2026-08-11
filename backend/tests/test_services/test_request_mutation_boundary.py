import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core import database
from app.core.context import reset_request_tenant_id, set_request_tenant_id
from app.main import app
from app.models.tenant import Account, Organization, Role, Tenant, TenantStatus, TenantType, account_roles
from app.services import auth as auth_service
from app.utils.security import hash_password


def _request(path: str = "/api/v1/accounts") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
        }
    )


@pytest.mark.anyio
async def test_locked_mutation_rejects_stale_account_version(db: AsyncSession):
    tenant = Tenant(name="边界租户", slug=f"boundary-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="总部")
    role = Role(tenant_id=tenant.id, name="admin")
    db.add_all([organization, role])
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="admin@boundary.test",
        hashed_password=hash_password("Password1"),
        name="管理员",
        auth_version=2,
    )
    db.add(account)
    await db.flush()
    await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=role.id))
    await db.flush()

    request = _request()
    request.state.auth_method = "jwt"
    request.state.account_id = str(account.id)
    request.state.auth_version = 1
    request.state.role = "admin"
    request.state.session_id = None
    request.state.original_tenant_id = None

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_mutating_principal(db, request, tenant.id)

    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_locked_mutation_rejects_revoked_durable_session(monkeypatch):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    request = _request()
    request.state.account_id = str(account_id)
    request.state.auth_version = 4
    request.state.session_id = str(uuid.uuid4())

    control_db = AsyncMock()
    control_db.scalar.return_value = None
    control_context = AsyncMock()
    control_context.__aenter__.return_value = control_db
    control_factory = MagicMock(return_value=control_context)
    monkeypatch.setattr(database, "control_session_factory", control_factory)

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_durable_session(request, tenant_id)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "登录会话已撤销或过期"
    control_db.scalar.assert_awaited_once()


@pytest.mark.anyio
async def test_locked_mutation_rejects_current_database_role_demotion(db: AsyncSession):
    tenant = Tenant(name="角色边界租户", slug=f"role-boundary-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="总部")
    admin_role = Role(tenant_id=tenant.id, name="admin")
    operator_role = Role(tenant_id=tenant.id, name="operator")
    db.add_all([organization, admin_role, operator_role])
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="demoted@boundary.test",
        hashed_password=hash_password("Password1"),
        name="已降权管理员",
    )
    db.add(account)
    await db.flush()
    await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=admin_role.id))
    await db.flush()
    await db.execute(
        account_roles.delete().where(
            account_roles.c.tenant_id == tenant.id,
            account_roles.c.account_id == account.id,
        )
    )
    await db.execute(
        account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=operator_role.id)
    )
    await db.flush()
    tenant_id = tenant.id
    account_id = account.id
    db.expire_all()

    request = _request()
    request.state.auth_method = "jwt"
    request.state.account_id = str(account_id)
    request.state.auth_version = 0
    request.state.role = "admin"
    request.state.session_id = None
    request.state.original_tenant_id = None

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_mutating_principal(db, request, tenant_id)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "账户已停用或登录状态已失效"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authorization", "expected_detail"),
    [
        (None, "代运营授权已失效"),
        (SimpleNamespace(scope=["analytics"], expires_at=None), "当前代运营授权不允许访问该功能"),
    ],
)
async def test_locked_mutation_rejects_revoked_or_narrowed_acting_authorization(
    monkeypatch,
    authorization,
    expected_detail,
):
    request = _request("/api/v1/products")
    request.state.acting_tenant_id = str(uuid.uuid4())
    request.state.original_tenant_id = str(uuid.uuid4())

    session = AsyncMock()
    session.scalar.return_value = authorization
    monkeypatch.setattr(database, "_apply_tenant_context", AsyncMock())
    monkeypatch.setattr(database, "_lock_agency_authorization_pair", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_acting_authorization(session, request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == expected_detail
    statement = session.scalar.await_args.args[0]
    assert statement._for_update_arg is None


@pytest.mark.anyio
async def test_acting_tenant_rows_use_stable_order_then_restore_target(monkeypatch):
    agency_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    client_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    request = _request()
    request.state.original_tenant_id = str(agency_id)
    request.state.acting_tenant_id = str(client_id)
    request.state.tenant_type = "agency"
    applied: list[uuid.UUID] = []

    async def record_context(_session, tenant_id):
        applied.append(tenant_id)

    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(status=TenantStatus.active, tenant_type=TenantType.brand),
        SimpleNamespace(status=TenantStatus.active, tenant_type=TenantType.agency),
    ]
    monkeypatch.setattr(database, "_apply_tenant_context", record_context)

    await database._lock_request_tenants(session, request, client_id)

    assert applied == [client_id, agency_id, client_id]


@pytest.mark.anyio
async def test_refresh_takes_session_serialization_key_before_auth_session_row(monkeypatch):
    session_id = uuid.uuid4()
    account = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), auth_version=0)
    existing_session = SimpleNamespace(id=session_id)
    events: list[str] = []

    async def lock_session(_db, locked_session_id):
        assert locked_session_id == session_id
        events.append("session-key")

    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_session

    async def execute(_statement):
        events.append("auth-session-row")
        return result

    db = AsyncMock()
    db.execute.side_effect = execute
    monkeypatch.setattr(database, "lock_auth_session_serialization", lock_session)

    resolved = await auth_service._lock_or_adopt_auth_session(
        db,
        payload={"sid": str(session_id), "jti": str(uuid.uuid4())},
        account=account,
    )

    assert resolved is existing_session
    assert events == ["session-key", "auth-session-row"]


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_acting_read_holds_live_principal_and_authorization_boundary_before_yield(monkeypatch, method):
    agency_id = uuid.uuid4()
    client_id = uuid.uuid4()
    session_id = uuid.uuid4()
    request = _request("/api/v1/products")
    request.scope["method"] = method
    request.state.original_tenant_id = str(agency_id)
    request.state.acting_tenant_id = str(client_id)
    request.state.session_id = str(session_id)
    request.state.tenant_type = "agency"
    events: list[str] = []

    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(database, "async_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(database, "_is_pg", False)
    monkeypatch.setattr(database, "_session_uses_postgresql", lambda _session: True)

    async def quota_lock(_session):
        events.append("quota")

    async def tenant_locks(_session, _request, locked_tenant_id):
        assert locked_tenant_id == client_id
        events.append("tenants")

    async def session_lock(_session, locked_session_id):
        assert locked_session_id == str(session_id)
        events.append("session")

    async def grant_lock(_session, _request):
        events.append("authorization")

    async def principal_lock(_session, _request, locked_tenant_id):
        assert locked_tenant_id == client_id
        events.append("principal")

    monkeypatch.setattr("app.services.quota.lock_quota_rollout_state", quota_lock)
    monkeypatch.setattr(database, "_lock_request_tenants", tenant_locks)
    monkeypatch.setattr(database, "lock_auth_session_serialization", session_lock)
    monkeypatch.setattr(database, "_revalidate_mutating_principal", principal_lock)
    monkeypatch.setattr(database, "_revalidate_acting_authorization", grant_lock)

    context_token = set_request_tenant_id(str(client_id))
    dependency = database.get_db(request)
    try:
        yielded = await anext(dependency)
        assert yielded is session
        assert events == ["quota", "tenants", "session", "principal", "authorization"]
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)
        session.commit.assert_awaited_once()
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)


@pytest.mark.anyio
async def test_api_key_mutation_revalidates_after_quota_tenant_and_session_locks(monkeypatch):
    tenant_id = uuid.uuid4()
    api_key_id = uuid.uuid4()
    request = _request("/open/v1/products")
    request.state.auth_method = "api_key"
    request.state.api_key_id = str(api_key_id)
    request.state.session_id = None
    request.state.tenant_type = "brand"
    events: list[str] = []

    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(database, "async_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(database, "_is_pg", False)
    monkeypatch.setattr(database, "_session_uses_postgresql", lambda _session: True)

    async def record(name):
        events.append(name)

    monkeypatch.setattr("app.services.quota.lock_quota_rollout_state", lambda _session: record("quota"))
    monkeypatch.setattr(database, "_lock_request_tenants", lambda *_args: record("tenants"))
    monkeypatch.setattr(database, "lock_auth_session_serialization", lambda *_args: record("session"))
    monkeypatch.setattr(database, "_revalidate_api_key_mutation", lambda *_args: record("api-key"), raising=False)
    monkeypatch.setattr(database, "_revalidate_mutating_principal", lambda *_args: record("principal"))
    monkeypatch.setattr(database, "_revalidate_acting_authorization", lambda *_args: record("authorization"))
    monkeypatch.setattr("app.services.entitlement.require_active_plan", lambda *_args, **_kwargs: record("plan"))

    context_token = set_request_tenant_id(str(tenant_id))
    dependency = database.get_db(request)
    try:
        assert await anext(dependency) is session
        assert events == ["quota", "tenants", "session", "api-key", "principal", "authorization", "plan"]
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)


@pytest.mark.anyio
async def test_locked_acting_read_rejects_removed_live_permission(monkeypatch):
    agency_id = uuid.uuid4()
    client_id = uuid.uuid4()
    account_id = uuid.uuid4()
    request = _request("/api/v1/products")
    request.scope["method"] = "GET"
    request.state.auth_method = "jwt"
    request.state.account_id = str(account_id)
    request.state.auth_version = 3
    request.state.role = "admin"
    request.state.permissions = ["product:read"]
    request.state.original_tenant_id = str(agency_id)

    live_account = SimpleNamespace(
        id=account_id,
        tenant_id=agency_id,
        auth_version=3,
        is_active=True,
        roles=[SimpleNamespace(name="admin", permissions=[])],
    )
    session = AsyncMock()
    session.scalar.return_value = live_account
    monkeypatch.setattr(database, "_revalidate_durable_session", AsyncMock())
    monkeypatch.setattr(database, "_apply_tenant_context", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_mutating_principal(session, request, client_id)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "账户已停用或登录状态已失效"


@pytest.mark.anyio
async def test_ordinary_read_uses_middleware_reload_without_serializing_tenant(monkeypatch):
    tenant_id = uuid.uuid4()
    request = _request("/api/v1/products")
    request.scope["method"] = "GET"
    request.state.tenant_type = "brand"

    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(database, "async_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(database, "_is_pg", False)
    tenant_locks = AsyncMock()
    monkeypatch.setattr(database, "_lock_request_tenants", tenant_locks)

    context_token = set_request_tenant_id(str(tenant_id))
    dependency = database.get_db(request)
    try:
        assert await anext(dependency) is session
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)
        tenant_locks.assert_not_awaited()
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "route_path"),
    [
        ("POST", "/api/v1/ops/authorizations"),
        ("DELETE", "/api/v1/ops/authorizations/{auth_id}"),
        ("POST", "/api/v1/agency/switch-context"),
    ],
)
async def test_agency_authorization_transition_dependency_defers_all_business_locks(
    monkeypatch,
    method: str,
    route_path: str,
):
    tenant_id = uuid.uuid4()
    request = _request("/api/v1/ops/authorizations")
    request.scope["method"] = method
    request.scope["route"] = SimpleNamespace(path=route_path)

    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(database, "async_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(database, "_is_pg", True)
    apply_context = AsyncMock()
    tenant_locks = AsyncMock()
    session_lock = AsyncMock()
    monkeypatch.setattr(database, "_apply_tenant_context", apply_context)
    monkeypatch.setattr(database, "_lock_request_tenants", tenant_locks)
    monkeypatch.setattr(database, "lock_auth_session_serialization", session_lock)

    context_token = set_request_tenant_id(str(tenant_id))
    dependency = database.get_db_for_agency_authorization_transition(request)
    try:
        assert await anext(dependency) is session
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)
        apply_context.assert_awaited_once_with(session, tenant_id)
        tenant_locks.assert_not_awaited()
        session_lock.assert_not_awaited()
        session.commit.assert_awaited_once()
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)


@pytest.mark.anyio
async def test_agency_authorization_transition_dependency_rejects_other_routes(monkeypatch):
    request = _request("/api/v1/products")
    request.scope["route"] = SimpleNamespace(path="/api/v1/products")
    session_factory = MagicMock()
    monkeypatch.setattr(database, "async_session_factory", session_factory)

    dependency = database.get_db_for_agency_authorization_transition(request)
    with pytest.raises(RuntimeError, match="outside its three approved routes"):
        await anext(dependency)
    session_factory.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "route_path"),
    [
        ("GET", "/api/v1/ops/authorizations"),
        ("POST", "/api/v1/ops/authorizations/{auth_id}"),
        ("DELETE", "/api/v1/agency/switch-context"),
    ],
)
async def test_agency_authorization_transition_dependency_rejects_wrong_method(
    method: str,
    route_path: str,
):
    request = _request(route_path)
    request.scope["method"] = method
    request.scope["route"] = SimpleNamespace(path=route_path)

    dependency = database.get_db_for_agency_authorization_transition(request)
    with pytest.raises(RuntimeError, match="outside its three approved routes"):
        await anext(dependency)


@pytest.mark.anyio
async def test_agency_authorization_transition_dependency_rolls_back_handler_failure(monkeypatch):
    tenant_id = uuid.uuid4()
    request = _request("/api/v1/ops/authorizations")
    request.scope["route"] = SimpleNamespace(path="/api/v1/ops/authorizations")
    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(database, "async_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(database, "_is_pg", False)

    context_token = set_request_tenant_id(str(tenant_id))
    dependency = database.get_db_for_agency_authorization_transition(request)
    try:
        assert await anext(dependency) is session
        with pytest.raises(ValueError, match="handler failed"):
            await dependency.athrow(ValueError("handler failed"))
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    finally:
        await dependency.aclose()
        reset_request_tenant_id(context_token)


def test_only_exact_agency_authorization_transitions_commit_before_response():
    registered_scopes = set()
    for route in app.routes:
        for dependency in getattr(route, "dependant", SimpleNamespace(dependencies=())).dependencies:
            if dependency.call is not database.get_db_for_agency_authorization_transition:
                continue
            for method in route.methods:
                registered_scopes.add((method, route.path, dependency.scope))

    assert registered_scopes == {
        ("POST", "/api/v1/ops/authorizations", "function"),
        ("DELETE", "/api/v1/ops/authorizations/{auth_id}", "function"),
        ("POST", "/api/v1/agency/switch-context", "function"),
    }


def test_only_expected_business_mutations_use_function_scoped_get_db():
    registered_scopes = set()
    for route in app.routes:
        for dependency in getattr(route, "dependant", SimpleNamespace(dependencies=())).dependencies:
            if dependency.call is not database.get_db or dependency.scope != "function":
                continue
            for method in route.methods:
                registered_scopes.add((method, route.path, dependency.scope))

    assert registered_scopes == {
        ("POST", "/api/v1/brands", "function"),
        ("PATCH", "/api/v1/brands/{brand_id}", "function"),
        ("DELETE", "/api/v1/brands/{brand_id}", "function"),
        ("POST", "/api/v1/products", "function"),
        ("PATCH", "/api/v1/products/{product_id}", "function"),
        ("POST", "/api/v1/products/{product_id}/assets", "function"),
        ("DELETE", "/api/v1/products/{product_id}", "function"),
        ("POST", "/api/v1/skus", "function"),
        ("PATCH", "/api/v1/skus/{sku_id}", "function"),
        ("DELETE", "/api/v1/skus/{sku_id}", "function"),
        ("POST", "/api/v1/production-batches", "function"),
        ("PATCH", "/api/v1/production-batches/{batch_id}", "function"),
        ("POST", "/api/v1/production-batches/{batch_id}/recall", "function"),
        ("POST", "/api/v1/production-batches/import-csv", "function"),
        ("DELETE", "/api/v1/production-batches/{batch_id}", "function"),
        ("PATCH", "/api/v1/product-assets/{asset_id}", "function"),
        ("DELETE", "/api/v1/product-assets/{asset_id}", "function"),
        ("POST", "/api/v1/code-batches", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/activate", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/export", "function"),
        ("PATCH", "/api/v1/code-batches/{batch_id}", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/freeze", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/void", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/mark-printing", "function"),
        ("POST", "/api/v1/code-batches/{batch_id}/mark-delivered", "function"),
        ("PATCH", "/api/v1/code-items/{item_id}", "function"),
        ("POST", "/api/v1/code-items/{item_id}/revoke", "function"),
        ("POST", "/api/v1/code-items/{item_id}/bind", "function"),
        ("POST", "/api/v1/risk-alerts/{alert_id}/resolve", "function"),
        ("POST", "/api/v1/risk-alerts/code-items/{item_id}/freeze", "function"),
        ("POST", "/api/v1/risk-alerts/code-items/{item_id}/unfreeze", "function"),
        ("POST", "/api/v1/imports/excel", "function"),
        ("POST", "/api/v1/imports/products", "function"),
        ("POST", "/api/v1/imports/existing-codes", "function"),
        ("POST", "/api/v1/page-templates/industry-templates/{index}/clone", "function"),
        ("POST", "/api/v1/page-templates/{template_id}/versions", "function"),
        ("POST", "/api/v1/page-templates/{template_id}/versions/{version_id}/rollback", "function"),
        ("POST", "/api/v1/webhooks/api-keys", "function"),
        ("POST", "/api/v1/webhooks/api-keys/{key_id}/rotate", "function"),
        ("DELETE", "/api/v1/webhooks/api-keys/{key_id}", "function"),
        ("POST", "/open/v1/products", "function"),
        ("PATCH", "/open/v1/products/{product_id}", "function"),
        ("POST", "/open/v1/skus", "function"),
        ("PATCH", "/open/v1/skus/{sku_id}", "function"),
        ("POST", "/open/v1/batches", "function"),
        ("GET", "/c/{public_id}", "function"),
        ("POST", "/api/v1/scan-events", "function"),
        ("POST", "/api/v1/takeovers", "function"),
        ("PATCH", "/api/v1/takeovers/{project_id}", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/assess", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/imports/dry-run", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/imports/{job_id}/submit", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/imports/{job_id}/retry-failed", "function"),
        ("GET", "/api/v1/takeovers/{project_id}/imports/{job_id}/errors.csv", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/domains/check", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/confirm", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/confirm", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/cutover", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/external-execution", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/probe", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/observe", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/complete", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/rollback", "function"),
        ("POST", "/api/v1/takeovers/{project_id}/routes/{route_id}/rollback/verify", "function"),
    }


def test_business_mutations_do_not_mix_request_and_function_scoped_get_db():
    mixed_scope_routes = set()

    def collect_get_db_scopes(dependency) -> list[str]:
        scopes = []
        if dependency.call is database.get_db:
            scopes.append(dependency.scope or "request")
        for child in dependency.dependencies:
            scopes.extend(collect_get_db_scopes(child))
        return scopes

    for route in app.routes:
        mutation_methods = set(getattr(route, "methods", ()) or ()) - {"GET", "HEAD", "OPTIONS"}
        if not mutation_methods:
            continue
        scopes = collect_get_db_scopes(route.dependant)
        if len(set(scopes)) > 1:
            for method in mutation_methods:
                mixed_scope_routes.add((method, route.path, tuple(scopes)))

    assert mixed_scope_routes == set()


def test_existing_code_import_uses_authoritative_parent_first_lock_order():
    from app.api.v1 import imports

    source = inspect.getsource(imports.import_existing_codes)
    production_batch_locator = source.index("select(CodeBatch.production_batch_id)")
    production_batch_lock = source.index("select(ProductionBatch)", production_batch_locator)
    code_batch_lock = source.index("select(CodeBatch)", production_batch_lock)
    code_item_write = source.index("CodeItem(", code_batch_lock)

    assert production_batch_locator < production_batch_lock < code_batch_lock < code_item_write
    assert ".with_for_update()" not in source[production_batch_locator:production_batch_lock]
    assert ".with_for_update()" in source[production_batch_lock:code_batch_lock]
    assert ".with_for_update()" in source[code_batch_lock:code_item_write]
    assert "select(CodeBatch, ProductionBatch)" not in source
    assert "map_code_batch_db_error(exc)" in source


def test_old_token_claim_locks_authoritative_chain_before_benefit_access():
    from app.api.v1 import benefit_claims

    source = inspect.getsource(benefit_claims.claim_benefit_h5)
    tenant_lock = source.index("lock_active_tenant_context")
    chain_locator = source.index("select(CodeItem.id, CodeItem.code_batch_id, CodeBatch.production_batch_id)")
    production_batch_lock = source.index("select(ProductionBatch)", chain_locator)
    code_batch_lock = source.index("select(CodeBatch)", production_batch_lock)
    code_item_lock = source.index("select(CodeItem)", code_batch_lock)
    benefit_access = source.index("select(Benefit)", code_item_lock)

    assert tenant_lock < chain_locator < production_batch_lock < code_batch_lock < code_item_lock < benefit_access
    assert ".with_for_update()" not in source[chain_locator:production_batch_lock]
    assert ".with_for_update()" in source[production_batch_lock:code_batch_lock]
    assert ".with_for_update()" in source[code_batch_lock:code_item_lock]
    assert ".with_for_update()" in source[code_item_lock:benefit_access]
    assert "select(CodeItem).join(" not in source


def test_excel_import_row_error_payload_exposes_only_safe_diagnostics():
    from app.api.v1 import imports
    from app.services.import_service import RowError

    payload = imports._row_error_payload(
        RowError(
            sheet="SKU",
            row=2,
            message="该行数据冲突，未完成导入",
            code="IMPORT_ROW_CONFLICT",
            reference_id="imp_0123456789abcdef0123456789abcdef",
        )
    )

    assert payload == {
        "sheet": "SKU",
        "row": 2,
        "message": "该行数据冲突，未完成导入",
        "code": "IMPORT_ROW_CONFLICT",
        "reference_id": "imp_0123456789abcdef0123456789abcdef",
    }


@pytest.mark.anyio
async def test_excel_import_audit_failure_is_not_swallowed(monkeypatch):
    from app.api.v1 import imports

    parsed = SimpleNamespace(errors=[], has_data=True)
    sheet = SimpleNamespace(total=1, created=1, updated=0, errors=[])
    report = SimpleNamespace(
        total_created=4,
        total_updated=0,
        total_errors=0,
        brands=sheet,
        products=sheet,
        skus=sheet,
        batches=sheet,
    )
    service = MagicMock()
    service.parse_and_validate = AsyncMock(return_value=parsed)
    service.execute_import = AsyncMock(return_value=report)
    monkeypatch.setattr(imports, "ExcelImportService", MagicMock(return_value=service))
    monkeypatch.setattr(imports, "_read_upload", AsyncMock(return_value=b"workbook"))
    monkeypatch.setattr(imports, "write_audit_log", AsyncMock(side_effect=RuntimeError("audit unavailable")))

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await imports.import_excel(
            file=MagicMock(),
            db=AsyncMock(),
            tenant_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            _role="admin",
            _permission=None,
        )


@pytest.mark.anyio
async def test_excel_import_uses_operation_uuid_audit_and_does_not_commit_in_route(monkeypatch):
    from app.api.v1 import imports

    parsed = SimpleNamespace(errors=[], has_data=True)
    sheet = SimpleNamespace(total=1, created=1, updated=0, errors=[])
    report = SimpleNamespace(
        total_created=4,
        total_updated=0,
        total_errors=0,
        brands=sheet,
        products=sheet,
        skus=sheet,
        batches=sheet,
    )
    service = MagicMock()
    service.parse_and_validate = AsyncMock(return_value=parsed)
    service.execute_import = AsyncMock(return_value=report)
    audit = AsyncMock()
    db = AsyncMock()
    monkeypatch.setattr(imports, "ExcelImportService", MagicMock(return_value=service))
    monkeypatch.setattr(imports, "_read_upload", AsyncMock(return_value=b"workbook"))
    monkeypatch.setattr(imports, "write_audit_log", audit)

    await imports.import_excel(
        file=MagicMock(),
        db=db,
        tenant_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        _role="admin",
        _permission=None,
    )

    audit_args = audit.await_args.args
    assert audit_args[3] == "catalog_import_completed"
    assert audit_args[4].startswith("catalog_import:")
    uuid.UUID(audit_args[4].removeprefix("catalog_import:"))
    assert audit_args[5] == {"created": 4, "updated": 0, "errors": 0, "import_type": "excel"}
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_product_csv_import_uses_strict_catalog_audit_shape(monkeypatch):
    from app.api.v1 import imports

    audit = AsyncMock()
    db = AsyncMock()
    monkeypatch.setattr(imports, "_read_upload", AsyncMock(return_value=b"product_name\n"))
    monkeypatch.setattr(imports, "write_audit_log", audit)

    await imports.import_products(
        file=MagicMock(),
        db=db,
        tenant_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        _role="admin",
        _permission=None,
    )

    audit_args = audit.await_args.args
    assert audit_args[3] == "catalog_import_completed"
    assert audit_args[4].startswith("catalog_import:")
    uuid.UUID(audit_args[4].removeprefix("catalog_import:"))
    assert audit_args[5] == {"created": 0, "updated": 0, "errors": 0, "import_type": "csv"}
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_product_csv_import_audit_failure_is_not_swallowed(monkeypatch):
    from app.api.v1 import imports

    monkeypatch.setattr(imports, "_read_upload", AsyncMock(return_value=b"product_name\n"))
    monkeypatch.setattr(imports, "write_audit_log", AsyncMock(side_effect=RuntimeError("audit unavailable")))

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await imports.import_products(
            file=MagicMock(),
            db=AsyncMock(),
            tenant_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            _role="admin",
            _permission=None,
        )


@pytest.mark.anyio
async def test_function_scoped_get_db_commit_failure_replaces_success_response(monkeypatch):
    events: list[str] = []

    class FailingCommitSession:
        async def commit(self) -> None:
            events.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            events.append("rollback")

    class SessionContext:
        async def __aenter__(self):
            return FailingCommitSession()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(database, "async_session_factory", lambda: SessionContext())
    monkeypatch.setattr(database, "_is_pg", False)
    probe = FastAPI()

    @probe.post("/api/v1/webhooks/api-keys")
    async def api_key_probe(_db=Depends(database.get_db, scope="function")):
        events.append("handler")
        return JSONResponse(status_code=201, content={"status": "success"})

    async with AsyncClient(
        transport=ASGITransport(app=probe, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/webhooks/api-keys")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert events == ["handler", "commit", "rollback"]


@pytest.mark.anyio
async def test_transition_commit_failure_replaces_success_before_asgi_response(monkeypatch):
    tenant_id = uuid.uuid4()
    events: list[str] = []

    class FailingCommitSession:
        async def commit(self) -> None:
            events.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            events.append("rollback")

    class SessionContext:
        async def __aenter__(self):
            return FailingCommitSession()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(database, "async_session_factory", lambda: SessionContext())
    monkeypatch.setattr(database, "_is_pg", False)

    probe = FastAPI()

    @probe.post("/api/v1/ops/authorizations")
    async def transition_probe(
        _db=Depends(database.get_db_for_agency_authorization_transition, scope="function"),
    ):
        events.append("handler")
        return JSONResponse(status_code=201, content={"status": "success"})

    context_token = set_request_tenant_id(str(tenant_id))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=probe, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/ops/authorizations")
    finally:
        reset_request_tenant_id(context_token)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert events == ["handler", "commit", "rollback"]
