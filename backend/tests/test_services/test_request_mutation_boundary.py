import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core import database
from app.models.tenant import Account, Organization, Role, Tenant, TenantStatus, account_roles
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

    control_db = AsyncMock()
    control_db.scalar.return_value = authorization
    control_context = AsyncMock()
    control_context.__aenter__.return_value = control_db
    monkeypatch.setattr(database, "control_session_factory", MagicMock(return_value=control_context))

    with pytest.raises(HTTPException) as exc_info:
        await database._revalidate_acting_authorization(request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == expected_detail


@pytest.mark.anyio
async def test_acting_tenant_rows_use_stable_order_then_restore_target(monkeypatch):
    agency_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    client_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    request = _request()
    request.state.original_tenant_id = str(agency_id)
    applied: list[uuid.UUID] = []

    async def record_context(_session, tenant_id):
        applied.append(tenant_id)

    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(status=TenantStatus.active)
    monkeypatch.setattr(database, "_apply_tenant_context", record_context)

    await database._lock_request_tenants(session, request, client_id)

    assert applied == [client_id, agency_id, client_id]
