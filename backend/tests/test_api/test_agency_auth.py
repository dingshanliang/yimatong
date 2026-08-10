"""Agency 授权 API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_for_agency_authorization_transition
from app.main import app
from app.models.auth_security import AuthSession
from app.models.campaign import Campaign
from app.models.connector import Connector
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    Organization,
    Permission,
    Role,
    Tenant,
    TenantStatus,
    TenantType,
    account_roles,
    role_permissions,
)
from app.services.connectors.secrets import encrypt_secrets
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import AUTH_SESSION_CACHE_PREFIX, create_access_token, decode_token
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
    app.dependency_overrides[get_db_for_agency_authorization_transition] = override_get_db
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
async def brand_headers(brand_tenant, db_session: AsyncSession):
    """品牌租户认证头"""
    tenant, account = brand_tenant
    session_id = uuid.uuid4()
    db_session.add(
        AuthSession(
            id=session_id,
            account_id=account.id,
            tenant_id=tenant.id,
            auth_version=account.auth_version,
            current_refresh_jti=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.flush()
    token = create_access_token(
        str(tenant.id),
        str(account.id),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": account.auth_version},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def agency_headers(agency_tenant, db_session: AsyncSession):
    """代运营租户认证头"""
    tenant, account = agency_tenant
    session_id = uuid.uuid4()
    db_session.add(
        AuthSession(
            id=session_id,
            account_id=account.id,
            tenant_id=tenant.id,
            auth_version=account.auth_version,
            current_refresh_jti=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.flush()
    token = create_access_token(
        str(tenant.id),
        str(account.id),
        "admin",
        "agency",
        extra={"sid": str(session_id), "auth_version": account.auth_version},
    )
    return {"Authorization": f"Bearer {token}"}


BASE_URL = "/api/v1/ops/authorizations"


async def _grant_fixed_role(
    db_session: AsyncSession,
    account: Account,
    role_name: str,
) -> None:
    role = Role(tenant_id=account.tenant_id, name=role_name, description=f"test {role_name}")
    permissions = [
        Permission(tenant_id=account.tenant_id, code=code, description=f"test {code}")
        for code in WEB_ROLE_PERMISSIONS[role_name]
    ]
    db_session.add_all([role, *permissions])
    await db_session.flush()
    await db_session.execute(account_roles.insert().values(account_id=account.id, role_id=role.id))
    if permissions:
        await db_session.execute(
            role_permissions.insert(),
            [{"role_id": role.id, "permission_id": permission.id} for permission in permissions],
        )
    await db_session.commit()


async def _durable_access_token(
    db_session: AsyncSession,
    tenant: Tenant,
    account: Account,
    role: str,
    *,
    session_id: uuid.UUID | None = None,
) -> str:
    durable_session_id = session_id or uuid.uuid4()
    db_session.add(
        AuthSession(
            id=durable_session_id,
            account_id=account.id,
            tenant_id=tenant.id,
            auth_version=account.auth_version,
            current_refresh_jti=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.flush()
    return create_access_token(
        str(tenant.id),
        str(account.id),
        role,
        tenant.tenant_type.value,
        extra={"auth_version": account.auth_version, "sid": str(durable_session_id)},
    )


class TestCreateAuthorization:
    @pytest.mark.anyio
    async def test_authorization_transition_rejects_legacy_token_without_session(
        self,
        client: AsyncClient,
        brand_tenant,
        agency_tenant,
    ):
        brand, account = brand_tenant
        agency, _ = agency_tenant
        legacy_token = create_access_token(str(brand.id), str(account.id), "admin", "brand")

        response = await client.post(
            BASE_URL,
            json={"agency_tenant_id": str(agency.id), "scope": ["pages"]},
            headers={"Authorization": f"Bearer {legacy_token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "登录会话已失效，请重新登录"

    @pytest.mark.anyio
    async def test_non_acting_agency_cannot_write_brand_resources(self, client: AsyncClient, agency_headers):
        response = await client.post(
            "/api/v1/products",
            json={"name": "越权产品"},
            headers=agency_headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "代运营服务商必须先进入已授权的客户工作区才能修改品牌数据"

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
        assert data["scope"] == ["products", "pages", "campaigns", "codes", "analytics"]

    @pytest.mark.anyio
    async def test_brand_can_authorize_by_business_workspace_slug(
        self, client: AsyncClient, brand_tenant, agency_tenant, brand_headers
    ):
        agency, _ = agency_tenant

        resp = await client.post(
            BASE_URL,
            json={"agency_slug": agency.slug, "scope": ["pages"]},
            headers=brand_headers,
        )

        assert resp.status_code == 201
        assert resp.json()["agency_tenant_id"] == str(agency.id)

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
    async def test_viewer_cannot_list_authorizations(self, client: AsyncClient, brand_tenant):
        tenant, account = brand_tenant
        token = create_access_token(str(tenant.id), str(account.id), "viewer", "brand")
        response = await client.get(BASE_URL, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403

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

    @pytest.mark.anyio
    async def test_excludes_expired_inactive_and_insufficient_scope(
        self, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        from app.services.agency_auth import get_authorized_client_ids

        brand, _ = brand_tenant
        agency, _ = agency_tenant
        auth = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=brand.id,
            scope=["pages"],
            status=AgencyAuthStatus.active,
            granted_at=datetime.now(UTC) - timedelta(minutes=2),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db_session.add(auth)
        await db_session.flush()
        assert await get_authorized_client_ids(db_session, agency.id) == []

        auth.expires_at = datetime.now(UTC) + timedelta(hours=1)
        await db_session.flush()
        assert await get_authorized_client_ids(db_session, agency.id, {"pages", "codes"}) == []

        auth.scope = ["pages", "codes"]
        brand.status = TenantStatus.suspended
        await db_session.flush()
        assert await get_authorized_client_ids(db_session, agency.id, {"pages", "codes"}) == []


class TestAgencyContextTokenVersion:
    @pytest.mark.anyio
    async def test_campaign_scope_reads_wecom_state_without_decrypting_callback_credentials(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        db_session.add_all(
            [
                AgencyAuthorization(
                    agency_tenant_id=agency.id,
                    client_tenant_id=brand.id,
                    scope=["campaigns"],
                    status=AgencyAuthStatus.active,
                ),
                Connector(
                    tenant_id=brand.id,
                    name="客户企微",
                    connector_type="wecom_customer_contact",
                    config={
                        "status": "connected",
                        "corp_id": "ww-client",
                        "customer_service_user_ids": ["member-safe"],
                    },
                    secrets_encrypted=encrypt_secrets(
                        {
                            "secret": "client-secret",
                            "callback_token": "must-not-be-read",
                            "encoding_aes_key": "must-not-be-read-either",
                        }
                    ),
                ),
            ]
        )
        await db_session.flush()
        await _grant_fixed_role(db_session, agency_account, "admin")
        token = await _durable_access_token(db_session, agency, agency_account, "admin")
        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}

        with (
            patch("app.core.database.async_session_factory", TestSessionLocal),
            patch(
                "app.services.wecom_integration.decrypt_secrets",
                side_effect=AssertionError("acting campaign scope must not decrypt connector secrets"),
            ),
        ):
            status = await client.get("/api/v1/integrations/wecom", headers=acting_headers)
            members = await client.get("/api/v1/integrations/wecom/members", headers=acting_headers)

        assert switched.status_code == 200
        assert status.status_code == 200
        assert status.json()["connected"] is True
        assert "secrets" not in status.json()
        assert members.status_code == 200
        assert members.json() == {"items": ["member-safe"]}

    @pytest.mark.anyio
    async def test_products_scope_reads_exact_category_dependency_without_tenant_write_access(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        brand.categories = ["粮油", "饮料"]
        db_session.add(
            AgencyAuthorization(
                agency_tenant_id=agency.id,
                client_tenant_id=brand.id,
                scope=["products"],
                status=AgencyAuthStatus.active,
            )
        )
        await db_session.commit()
        token = await _durable_access_token(db_session, agency, agency_account, "admin")

        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}

        with patch("app.core.database.async_session_factory", TestSessionLocal):
            categories = await client.get("/api/v1/tenants/me/categories", headers=acting_headers)
            tenant_write = await client.patch(
                "/api/v1/tenants/me",
                json={"categories": ["越权品类"]},
                headers=acting_headers,
            )
            category_subpath = await client.get(
                "/api/v1/tenants/me/categories/export",
                headers=acting_headers,
            )

        assert switched.status_code == 200
        assert categories.status_code == 200
        assert categories.json() == {"categories": ["粮油", "饮料"]}
        assert tenant_write.status_code == 403
        assert category_subpath.status_code == 403

    @pytest.mark.anyio
    async def test_acting_workspace_reads_client_entitlement_without_exposing_tenant_profile(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        brand.plan = "pro"
        brand.plan_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.add(
            AgencyAuthorization(
                agency_tenant_id=agency.id,
                client_tenant_id=brand.id,
                scope=["campaigns"],
                status=AgencyAuthStatus.active,
            )
        )
        await db_session.flush()
        await _grant_fixed_role(db_session, agency_account, "admin")
        token = await _durable_access_token(db_session, agency, agency_account, "admin")
        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}

        with patch("app.core.database.async_session_factory", TestSessionLocal):
            entitlement = await client.get("/api/v1/tenants/me/entitlement", headers=acting_headers)
            profile = await client.get("/api/v1/tenants/me", headers=acting_headers)

        assert entitlement.status_code == 200
        assert entitlement.json() == {
            "tenant_id": str(brand.id),
            "plan": "pro",
            "plan_expires_at": brand.plan_expires_at.isoformat().replace("+00:00", "Z"),
            "read_only": True,
            "enabled_features": {
                "ai_assistant": False,
                "risk_module": False,
                "channel_portal": False,
                "white_label": False,
                "cash_red_packet": False,
            },
        }
        assert profile.status_code == 403

    @pytest.mark.anyio
    async def test_switch_and_exit_preserve_validated_auth_version(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant, shared_security_cache
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        agency_account.auth_version = 7
        db_session.add(
            AgencyAuthorization(
                agency_tenant_id=agency.id,
                client_tenant_id=brand.id,
                scope=["pages"],
                status=AgencyAuthStatus.active,
            )
        )
        await db_session.commit()
        session_id = uuid.uuid4()
        token = await _durable_access_token(
            db_session,
            agency,
            agency_account,
            "admin",
            session_id=session_id,
        )

        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert switched.status_code == 200
        assert any(
            "access_token=" in cookie and "httponly" in cookie.lower()
            for cookie in switched.headers.get_list("set-cookie")
        )
        switched_payload = decode_token(switched.json()["access_token"])
        assert switched_payload["auth_version"] == 7
        assert switched_payload["sid"] == str(session_id)

        with patch("app.core.database.async_session_factory", TestSessionLocal):
            exited = await client.post(
                "/api/v1/agency/exit-context",
                headers={"Authorization": f"Bearer {switched.json()['access_token']}"},
            )
        assert exited.status_code == 200
        assert any(
            "access_token=" in cookie and "httponly" in cookie.lower()
            for cookie in exited.headers.get_list("set-cookie")
        )
        exited_payload = decode_token(exited.json()["access_token"])
        assert exited_payload["auth_version"] == 7
        assert exited_payload["sid"] == str(session_id)

        shared_security_cache.revoked_jtis.add(f"{AUTH_SESSION_CACHE_PREFIX}{session_id}")
        rejected = await client.post(
            "/api/v1/agency/exit-context",
            headers={"Authorization": f"Bearer {switched.json()['access_token']}"},
        )
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "Invalid or expired token"}

    @pytest.mark.anyio
    async def test_revoked_authorization_blocks_client_access_but_allows_strict_exit(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        authorization = AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=brand.id,
            scope=["pages"],
            status=AgencyAuthStatus.active,
        )
        db_session.add(authorization)
        await db_session.commit()
        token = await _durable_access_token(db_session, agency, agency_account, "admin")
        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert switched.status_code == 200

        authorization.status = AgencyAuthStatus.revoked
        authorization.revoked_at = datetime.now(UTC)
        await db_session.commit()
        acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}
        with patch("app.core.database.async_session_factory", TestSessionLocal):
            denied = await client.get("/api/v1/page-templates", headers=acting_headers)
            exited = await client.post("/api/v1/agency/exit-context", headers=acting_headers)

        assert denied.status_code == 403
        assert exited.status_code == 200
        payload = decode_token(exited.json()["access_token"])
        assert payload["tenant_id"] == str(agency.id)
        assert "acting_tenant_id" not in payload

    @pytest.mark.anyio
    async def test_exit_still_rejects_inactive_original_agency_account(
        self, client: AsyncClient, db_session: AsyncSession, brand_tenant, agency_tenant
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        db_session.add(
            AgencyAuthorization(
                agency_tenant_id=agency.id,
                client_tenant_id=brand.id,
                scope=["pages"],
                status=AgencyAuthStatus.active,
            )
        )
        await db_session.commit()
        token = await _durable_access_token(db_session, agency, agency_account, "admin")
        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert switched.status_code == 200

        agency_account.is_active = False
        await db_session.commit()
        with patch("app.core.database.async_session_factory", TestSessionLocal):
            exited = await client.post(
                "/api/v1/agency/exit-context",
                headers={"Authorization": f"Bearer {switched.json()['access_token']}"},
            )

        assert exited.status_code == 401


class TestViewerAndCampaignAuthorizationBoundary:
    @pytest.mark.anyio
    async def test_brand_viewer_cannot_create_campaign_even_if_account_has_admin_permissions(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        brand_tenant,
    ):
        brand, account = brand_tenant
        await _grant_fixed_role(db_session, account, "admin")
        viewer_token = create_access_token(str(brand.id), str(account.id), "viewer", "brand")

        response = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "viewer 越权活动",
                "campaign_type": "coupon",
                "start_at": "2026-08-01T00:00:00",
                "end_at": "2026-08-31T23:59:59",
                "rules_json": {},
            },
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission: campaign:create"

    @pytest.mark.anyio
    async def test_agency_viewer_cannot_switch_but_operator_can(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        brand_tenant,
        agency_tenant,
    ):
        brand, _ = brand_tenant
        agency, account = agency_tenant
        db_session.add(
            AgencyAuthorization(
                agency_tenant_id=agency.id,
                client_tenant_id=brand.id,
                scope=["campaigns"],
                status=AgencyAuthStatus.active,
            )
        )
        await db_session.commit()

        viewer_token = create_access_token(str(agency.id), str(account.id), "viewer", "agency")
        operator_token = await _durable_access_token(db_session, agency, account, "operator")
        viewer_response = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        operator_response = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {operator_token}"},
        )

        assert viewer_response.status_code == 403
        assert operator_response.status_code == 200

    @pytest.mark.anyio
    async def test_acting_admin_can_create_for_client_but_cannot_patch_other_tenant_campaign(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        brand_tenant,
        agency_tenant,
    ):
        brand, _ = brand_tenant
        agency, agency_account = agency_tenant
        other_tenant = Tenant(
            name="其他品牌",
            slug=f"other-brand-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.brand,
        )
        db_session.add(other_tenant)
        await db_session.flush()
        other_campaign = Campaign(
            tenant_id=other_tenant.id,
            name="其他租户活动",
            campaign_type="coupon",
            start_at="2026-08-01T00:00:00",
            end_at="2026-08-31T23:59:59",
            rules_json={},
        )
        db_session.add_all(
            [
                other_campaign,
                AgencyAuthorization(
                    agency_tenant_id=agency.id,
                    client_tenant_id=brand.id,
                    scope=["campaigns"],
                    status=AgencyAuthStatus.active,
                ),
            ]
        )
        await db_session.flush()
        await _grant_fixed_role(db_session, agency_account, "admin")

        admin_token = await _durable_access_token(db_session, agency, agency_account, "admin")
        switched = await client.post(
            "/api/v1/agency/switch-context",
            json={"client_tenant_id": str(brand.id)},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        acting_headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}
        with patch("app.core.database.async_session_factory", TestSessionLocal):
            created = await client.post(
                "/api/v1/campaigns",
                json={
                    "name": "合法代运营活动",
                    "campaign_type": "coupon",
                    "start_at": "2026-08-01T00:00:00",
                    "end_at": "2026-08-31T23:59:59",
                    "rules_json": {
                        "participation_conditions": "扫码参与",
                        "claim_limits": "每人限领1次",
                        "validity_period": "7天",
                        "disclaimer": "品牌方保留解释权",
                        "minor_notice": "未成年人需监护人陪同",
                        "customer_service_contact": "400-000-0000",
                    },
                },
                headers=acting_headers,
            )
            cross_tenant = await client.patch(
                f"/api/v1/campaigns/{other_campaign.id}",
                json={"name": "越权修改"},
                headers=acting_headers,
            )

        assert switched.status_code == 200
        assert created.status_code == 201
        assert cross_tenant.status_code == 404
