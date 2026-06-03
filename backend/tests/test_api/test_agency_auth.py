"""Agency 授权 API 测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    Organization,
    Tenant,
    TenantStatus,
    TenantType,
)
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


@pytest.fixture
async def brand_tenant(db_session: AsyncSession):
    """创建品牌租户 + 组织 + 账号"""
    tenant = Tenant(
        name="测试品牌",
        slug=f"test-brand-{uuid.uuid4().hex[:8]}",
        status=TenantStatus.active,
        tenant_type=TenantType.brand,
    )
    db_session.add(tenant)
    await db_session.flush()

    org = Organization(tenant_id=tenant.id, name="品牌组织")
    db_session.add(org)
    await db_session.flush()

    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email="brand@test.com",
        hashed_password="hashed",
        name="Brand Admin",
    )
    db_session.add(account)
    await db_session.flush()

    return tenant, account


@pytest.fixture
async def agency_tenant(db_session: AsyncSession):
    """创建代运营租户 + 组织 + 账号"""
    tenant = Tenant(
        name="测试代运营",
        slug=f"test-agency-{uuid.uuid4().hex[:8]}",
        status=TenantStatus.active,
        tenant_type=TenantType.agency,
    )
    db_session.add(tenant)
    await db_session.flush()

    org = Organization(tenant_id=tenant.id, name="代运营组织")
    db_session.add(org)
    await db_session.flush()

    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email="agency@test.com",
        hashed_password="hashed",
        name="Agency Admin",
    )
    db_session.add(account)
    await db_session.flush()

    return tenant, account


@pytest.fixture
def brand_headers(brand_tenant):
    """品牌租户认证头"""
    tenant, account = brand_tenant
    token = create_access_token(str(tenant.id), str(account.id), "admin", "brand")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def agency_headers(agency_tenant):
    """代运营租户认证头"""
    tenant, account = agency_tenant
    token = create_access_token(str(tenant.id), str(account.id), "admin", "agency")
    return {"Authorization": f"Bearer {token}"}


BASE_URL = "/api/v1/ops/authorizations"


class TestCreateAuthorization:
    @pytest.mark.anyio
    async def test_brand_authorize_agency(self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers):
        """Brand 授权 agency 成功"""
        brand, _ = brand_tenant
        agency, _ = agency_tenant

        resp = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency.id),
                "scope": ["pages", "campaigns", "analytics"],
            },
            headers=brand_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert str(data["agency_tenant_id"]) == str(agency.id)
        assert str(data["client_tenant_id"]) == str(brand.id)
        assert data["scope"] == ["pages", "campaigns", "analytics"]
        assert data["status"] == "active"

    @pytest.mark.anyio
    async def test_brand_authorize_agency_default_scope(
        self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers
    ):
        """Brand 授权 agency 使用默认 scope"""
        agency, _ = agency_tenant

        resp = await client.post(
            BASE_URL,
            json={"agency_tenant_id": str(agency.id)},
            headers=brand_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scope"] == ["pages", "campaigns", "analytics"]

    @pytest.mark.anyio
    async def test_agency_cannot_create_authorization(self, client: AsyncClient, brand_tenant, agency_headers):
        """Agency 不能创建授权（403）"""
        brand, _ = brand_tenant

        resp = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(brand.id),
                "scope": ["pages"],
            },
            headers=agency_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_re_authorize_updates_scope(self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers):
        """重复授权更新 scope"""
        agency, _ = agency_tenant

        # First authorization
        resp1 = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency.id),
                "scope": ["pages"],
            },
            headers=brand_headers,
        )
        assert resp1.status_code == 201

        # Re-authorize with different scope
        resp2 = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency.id),
                "scope": ["pages", "campaigns", "analytics"],
            },
            headers=brand_headers,
        )
        assert resp2.status_code == 201
        data = resp2.json()
        assert data["scope"] == ["pages", "campaigns", "analytics"]


class TestListAuthorizations:
    @pytest.mark.anyio
    async def test_list_as_brand(self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers):
        """Brand 查看授权列表"""
        brand, _ = brand_tenant
        agency, _ = agency_tenant

        # Create authorization first
        await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency.id),
                "scope": ["pages", "campaigns"],
            },
            headers=brand_headers,
        )

        resp = await client.get(BASE_URL, headers=brand_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["agency_name"] == "测试代运营"
        assert data["items"][0]["scope"] == ["pages", "campaigns"]

    @pytest.mark.anyio
    async def test_list_as_agency(
        self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers, agency_headers
    ):
        """Agency 查看授权列表"""
        brand, _ = brand_tenant

        # Brand authorizes agency
        await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency_tenant[0].id),
                "scope": ["analytics"],
            },
            headers=brand_headers,
        )

        # Agency lists its authorizations
        resp = await client.get(BASE_URL, headers=agency_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["client_name"] == "测试品牌"
        assert data["items"][0]["scope"] == ["analytics"]


class TestRevokeAuthorization:
    @pytest.mark.anyio
    async def test_revoke(self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers):
        """Brand 撤销授权"""
        # Create authorization
        create_resp = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency_tenant[0].id),
                "scope": ["pages"],
            },
            headers=brand_headers,
        )
        auth_id = create_resp.json()["id"]

        # Revoke
        revoke_resp = await client.delete(
            f"{BASE_URL}/{auth_id}",
            headers=brand_headers,
        )
        assert revoke_resp.status_code == 204

    @pytest.mark.anyio
    async def test_revoke_nonexistent(self, client: AsyncClient, brand_headers):
        """撤销不存在的授权返回 404"""
        fake_id = str(uuid.uuid4())
        resp = await client.delete(
            f"{BASE_URL}/{fake_id}",
            headers=brand_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_agency_cannot_revoke(
        self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers, agency_headers
    ):
        """Agency 不能撤销授权（403）"""
        create_resp = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency_tenant[0].id),
                "scope": ["pages"],
            },
            headers=brand_headers,
        )
        auth_id = create_resp.json()["id"]

        resp = await client.delete(
            f"{BASE_URL}/{auth_id}",
            headers=agency_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_revoked_auth_not_in_list(self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers):
        """已撤销的授权不出现在列表中"""
        # Create authorization
        create_resp = await client.post(
            BASE_URL,
            json={
                "agency_tenant_id": str(agency_tenant[0].id),
                "scope": ["pages"],
            },
            headers=brand_headers,
        )
        auth_id = create_resp.json()["id"]

        # Revoke it
        await client.delete(f"{BASE_URL}/{auth_id}", headers=brand_headers)

        # Verify it's gone from list
        list_resp = await client.get(BASE_URL, headers=brand_headers)
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0


class TestVerifyAuthorization:
    @pytest.mark.anyio
    async def test_verify_active_auth(self, db_session: AsyncSession, brand_tenant, agency_tenant):
        """验证活跃授权成功"""
        from app.services.agency_auth import verify_authorization

        brand, _ = brand_tenant
        agency, _ = agency_tenant

        auth = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=brand.id,
            scope=["pages", "campaigns"],
            status=AgencyAuthStatus.active,
        )
        db_session.add(auth)
        await db_session.flush()

        result = await verify_authorization(db_session, agency.id, brand.id)
        assert result is not None
        assert result.status == AgencyAuthStatus.active

    @pytest.mark.anyio
    async def test_verify_with_scope_check(self, db_session: AsyncSession, brand_tenant, agency_tenant):
        """验证 scope 检查"""
        from app.services.agency_auth import verify_authorization

        brand, _ = brand_tenant
        agency, _ = agency_tenant

        auth = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=brand.id,
            scope=["pages"],
            status=AgencyAuthStatus.active,
        )
        db_session.add(auth)
        await db_session.flush()

        # Has scope
        result = await verify_authorization(db_session, agency.id, brand.id, "pages")
        assert result is not None

        # Missing scope
        result = await verify_authorization(db_session, agency.id, brand.id, "analytics")
        assert result is None


class TestGetAuthorizedClientIds:
    @pytest.mark.anyio
    async def test_get_authorized_client_ids(self, db_session: AsyncSession, brand_tenant, agency_tenant):
        """获取 agency 被授权的客户 ID 列表"""
        from app.services.agency_auth import get_authorized_client_ids

        brand, _ = brand_tenant
        agency, _ = agency_tenant

        auth = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=brand.id,
            scope=["pages"],
            status=AgencyAuthStatus.active,
        )
        db_session.add(auth)
        await db_session.flush()

        client_ids = await get_authorized_client_ids(db_session, agency.id)
        assert len(client_ids) == 1
        assert client_ids[0] == brand.id
