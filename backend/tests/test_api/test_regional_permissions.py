"""回归测试：区域品牌/协会管理端点必须挂 RBAC 权限依赖。

历史缺陷：regional.py 全部路由只依赖 get_current_tenant（仅认证、无授权），
任意已登录角色（含 viewer、渠道门户身份）都能创建组织、增删成员、下发模板、
改数据隔离策略和白标/域名配置 → 水平越权。

修复约定：写路由挂 channel:manage、读路由挂 channel:read（均为已有权限码，
admin/operator 持有；viewer 无任何权限码）。
"""

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace

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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _auth_headers(tenant_id: uuid.UUID | str, account_id: uuid.UUID | str, role: str = "admin") -> dict:
    token = create_access_token(str(tenant_id), str(account_id), role)
    return {"Authorization": f"Bearer {token}"}


# (方法, 路径, 业务层预期状态码)：路径携带随机 org_id，admin 过权限层后命中业务 404。
WRITE_ROUTES = [
    ("post", "/api/v1/regional/orgs", 201),
    ("post", "/api/v1/regional/orgs/{org_id}/members", 404),
    ("put", "/api/v1/regional/orgs/{org_id}/members/{member_id}", 404),
    ("delete", "/api/v1/regional/orgs/{org_id}/members/{member_id}", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/templates", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/templates/{template_id}/publish", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/products", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/code-rules", 404),
    ("put", "/api/v1/regional/orgs/{org_id}/whitelabel", 404),
    ("put", "/api/v1/regional/orgs/{org_id}/whitelabel-config", 404),
    ("put", "/api/v1/regional/orgs/{org_id}/data-policy", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/unified-campaigns", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/domains", 404),
    ("post", "/api/v1/regional/orgs/{org_id}/domains/{domain_id}/verify", 404),
    ("delete", "/api/v1/regional/orgs/{org_id}/domains/{domain_id}", 404),
]

READ_ROUTES = [
    ("get", "/api/v1/regional/orgs", 200),
    ("get", "/api/v1/regional/orgs/{org_id}", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/members", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/templates", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/code-rules", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/dashboard", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/advanced-dashboard", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/unified-campaigns", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/data-policy", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/whitelabel", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/whitelabel-config", 404),
    ("get", "/api/v1/regional/orgs/{org_id}/domains", 404),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path", "_business_status"), WRITE_ROUTES)
async def test_viewer_cannot_touch_regional_write_routes(
    client: AsyncClient, method: str, path: str, _business_status: str
):
    """viewer 无 channel:manage，全部区域组织写路由必须在业务层之前 403。"""
    tenant_id = uuid.uuid4()
    url = path.format(org_id=uuid.uuid4(), member_id=uuid.uuid4(), template_id=uuid.uuid4(), domain_id=uuid.uuid4())
    response = await client.request(method, url, json={}, headers=_auth_headers(tenant_id, uuid.uuid4(), "viewer"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: channel:manage"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path", "_business_status"), READ_ROUTES)
async def test_viewer_cannot_read_regional_routes(client: AsyncClient, method: str, path: str, _business_status: str):
    """viewer 无 channel:read，区域组织读路由同样 403（viewer 角色无任何权限码）。"""
    tenant_id = uuid.uuid4()
    url = path.format(org_id=uuid.uuid4(), member_id=uuid.uuid4(), template_id=uuid.uuid4(), domain_id=uuid.uuid4())
    response = await client.request(method, url, headers=_auth_headers(tenant_id, uuid.uuid4(), "viewer"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: channel:read"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path", "business_status"), WRITE_ROUTES[1:])
async def test_admin_passes_permission_layer_and_reaches_business_layer(
    client: AsyncClient, method: str, path: str, business_status: int
):
    """admin 持有 channel:manage：过权限层后到达业务层（404 不存在 / 422 校验失败都算过权限层）。"""
    tenant_id = uuid.uuid4()
    url = path.format(org_id=uuid.uuid4(), member_id=uuid.uuid4(), template_id=uuid.uuid4(), domain_id=uuid.uuid4())
    response = await client.request(method, url, json={}, headers=_auth_headers(tenant_id, uuid.uuid4(), "admin"))
    assert response.status_code != 403
    assert response.status_code in (business_status, 422)


@pytest.mark.anyio
async def test_admin_can_create_regional_org(client: AsyncClient):
    """POST /regional/orgs 曾完全无授权；admin 现在必须过 channel:manage 后进入业务层。"""
    tenant_id = uuid.uuid4()
    org = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, name="赣南脐橙协会", org_type="association", config={})
    summary = {"id": str(org.id), "name": org.name, "org_type": org.org_type}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.api.v1.regional.create_regional_org", _async_return(org))
        mp.setattr("app.api.v1.regional.regional_org_summary", _async_return(summary))
        response = await client.post(
            "/api/v1/regional/orgs",
            json={"name": "赣南脐橙协会", "org_type": "association"},
            headers=_auth_headers(tenant_id, uuid.uuid4(), "admin"),
        )

    assert response.status_code == 201
    assert response.json()["id"] == summary["id"]


def _async_return(value):
    async def _factory(*_args, **_kwargs):
        return value

    return _factory
