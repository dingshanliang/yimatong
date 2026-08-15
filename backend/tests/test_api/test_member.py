"""W12: 轻量会员与积分测试"""

import hashlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import members as members_api
from app.core.database import get_db, get_db_for_consumer, get_db_with_bypass
from app.main import app
from app.middleware.tenant import TenantScopeMiddleware
from app.models.auth_security import AuthSession
from app.models.code import CodeBatch, CodeItem, CodeItemStatus
from app.models.consent import ConsumerConsentAction, ConsumerConsentPolicy, ConsumerConsentPolicyCurrent
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.models.tenant import Account, Organization, Permission, Role, Tenant, role_permissions
from app.models.visitor import AnonymousVisitor
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from app.utils.crypto import encrypt_consumer_phone, hash_phone
from app.utils.security import create_access_token, decode_token, hash_password
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    app.dependency_overrides[get_db_for_consumer] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "会员测试租户",
            "admin_email": "member@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "member@test.com", "password": "Pass1234"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return tid, headers


async def _login_member_role(
    client: AsyncClient,
    db_session: AsyncSession,
    tenant_id: str,
    *,
    role_name: str,
) -> tuple[dict[str, str], uuid.UUID]:
    tenant_uuid = uuid.UUID(tenant_id)
    role = await db_session.scalar(select(Role).where(Role.tenant_id == tenant_uuid, Role.name == role_name))
    organization = await db_session.scalar(select(Organization).where(Organization.tenant_id == tenant_uuid))
    assert role is not None and organization is not None
    account = Account(
        tenant_id=tenant_uuid,
        organization_id=organization.id,
        email=f"member-{role_name}-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("Pass1234"),
        name=f"Member {role_name}",
        roles=[role],
    )
    db_session.add(account)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"email": account.email, "password": "Pass1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, account.id


MEMBER_DETAIL_READ_CASES = [
    pytest.param("/api/v1/members/overview", "get_member_overview", {}, id="overview"),
    pytest.param(
        "/api/v1/members/consumers/search?keyword=test",
        "search_consumers",
        [],
        id="consumer-search",
    ),
    pytest.param(
        "/api/v1/members/consumers/00000000-0000-0000-0000-000000000001",
        "get_consumer_profile",
        {"id": "00000000-0000-0000-0000-000000000001", "recent_transactions": []},
        id="consumer-detail",
    ),
    pytest.param(
        "/api/v1/members/consumers/00000000-0000-0000-0000-000000000001/transactions",
        "list_point_transactions",
        ([], 0),
        id="transactions",
    ),
    pytest.param(
        "/api/v1/members/point-redemptions",
        "list_point_redemptions",
        ([], 0),
        id="redemptions",
    ),
]


async def create_scan_context(db_session: AsyncSession, tenant_id: str, consumer_id: str = "") -> str:
    batch_id = uuid.uuid4()
    production_batch_id = uuid.uuid4()
    public_id = f"TEST{uuid.uuid4().hex[:10]}"
    db_session.add(
        CodeBatch(
            id=batch_id,
            tenant_id=uuid.UUID(tenant_id),
            product_id=uuid.uuid4(),
            sku_id=uuid.uuid4(),
            production_batch_id=production_batch_id,
            batch_code=f"B-{public_id}",
            quantity=1,
            status="activated",
            code_type="single",
            created_by=uuid.uuid4(),
        )
    )
    db_session.add(
        CodeItem(
            tenant_id=uuid.UUID(tenant_id),
            code_batch_id=batch_id,
            public_id=public_id,
            status=CodeItemStatus.activated,
            code_type="single",
        )
    )
    await db_session.flush()
    return create_scan_token(
        public_id,
        compute_ip_hash("127.0.0.1"),
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )


class TestMemberDetailReadAuthority:
    @pytest.mark.anyio
    @pytest.mark.parametrize("path,service_name,service_result", MEMBER_DETAIL_READ_CASES)
    @pytest.mark.parametrize("role_name", ["admin", "operator"])
    async def test_live_consumer_detail_permission_allows_member_reads(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        service_name: str,
        service_result,
        role_name: str,
    ):
        tenant_id, admin_headers = setup_tenant
        headers = admin_headers
        if role_name == "operator":
            headers, _account_id = await _login_member_role(
                client,
                db_session,
                tenant_id,
                role_name=role_name,
            )
        service_spy = AsyncMock(return_value=service_result)
        monkeypatch.setattr(members_api, service_name, service_spy)

        response = await client.get(path, headers=headers)

        assert response.status_code == 200
        service_spy.assert_awaited_once()

    @pytest.mark.anyio
    @pytest.mark.parametrize("path,service_name,service_result", MEMBER_DETAIL_READ_CASES)
    @pytest.mark.parametrize("denied_authority", ["viewer", "revoked_permission", "revoked_session"])
    async def test_denied_member_reads_never_enter_route_database_or_service(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        service_name: str,
        service_result,
        denied_authority: str,
    ):
        tenant_id, headers = setup_tenant
        tenant_uuid = uuid.UUID(tenant_id)
        if denied_authority == "viewer":
            headers, _account_id = await _login_member_role(
                client,
                db_session,
                tenant_id,
                role_name="viewer",
            )
        elif denied_authority == "revoked_permission":
            admin_role_id = await db_session.scalar(
                select(Role.id).where(Role.tenant_id == tenant_uuid, Role.name == "admin")
            )
            permission_id = await db_session.scalar(
                select(Permission.id).where(
                    Permission.tenant_id == tenant_uuid,
                    Permission.code == "consumer:detail",
                )
            )
            assert admin_role_id is not None and permission_id is not None
            await db_session.execute(
                delete(role_permissions).where(
                    role_permissions.c.tenant_id == tenant_uuid,
                    role_permissions.c.role_id == admin_role_id,
                    role_permissions.c.permission_id == permission_id,
                )
            )
            await db_session.commit()

            async def load_revoked_permission(
                _middleware,
                _account_id,
                _role,
                _token_auth_version,
                _tenant_id,
                _expected_tenant_type=None,
            ):
                # The default SQLite middleware factory cannot see fixture-session
                # permission mutations; emulate its production PG live lookup result.
                return True, []

            monkeypatch.setattr(TenantScopeMiddleware, "_load_account_access", load_revoked_permission)
        else:
            payload = decode_token(headers["Authorization"].removeprefix("Bearer "))
            session = await db_session.get(AuthSession, uuid.UUID(payload["sid"]))
            assert session is not None
            session.revoked_at = datetime.now(UTC)
            await db_session.commit()

            async def load_revoked_session(
                _middleware,
                _session_id,
                _account_id,
                _tenant_id,
                _token_auth_version,
            ):
                return False

            monkeypatch.setattr(TenantScopeMiddleware, "_load_auth_session_access", load_revoked_session)

        service_spy = AsyncMock(return_value=service_result)
        monkeypatch.setattr(members_api, service_name, service_spy)
        route_db_calls = 0

        async def denied_route_db():
            nonlocal route_db_calls
            route_db_calls += 1
            raise AssertionError("member route database dependency ran before authorization")
            yield  # pragma: no cover

        original_get_db = app.dependency_overrides[get_db]
        app.dependency_overrides[get_db] = denied_route_db
        try:
            response = await client.get(path, headers=headers)
        finally:
            app.dependency_overrides[get_db] = original_get_db

        assert response.status_code == (401 if denied_authority == "revoked_session" else 403)
        assert route_db_calls == 0
        service_spy.assert_not_awaited()

    @pytest.mark.anyio
    async def test_consumer_detail_read_is_non_enumerating_across_tenants(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _source_tenant_id, source_headers = setup_tenant
        created = await client.post("/api/v1/members/consumers", json={}, headers=source_headers)
        assert created.status_code == 201
        consumer_id = created.json()["id"]
        other_email = f"isolated-member-{uuid.uuid4().hex[:8]}@test.com"
        other = await client.post(
            "/api/v1/tenants",
            json={
                "name": "会员读取隔离租户",
                "admin_email": other_email,
                "admin_name": "Isolated Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert other.status_code == 201
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": other_email, "password": "Pass1234"},
        )
        assert login.status_code == 200
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = await client.get(f"/api/v1/members/consumers/{consumer_id}", headers=other_headers)

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_public_consumer_points_read_remains_scan_token_self_service(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tenant_id, admin_headers = setup_tenant
        created = await client.post("/api/v1/members/consumers", json={}, headers=admin_headers)
        assert created.status_code == 201
        consumer_id = created.json()["id"]
        token = await create_scan_context(db_session, tenant_id, consumer_id=consumer_id)

        response = await client.get(
            "/api/v1/consumers/points/me",
            params={"consumer_id": consumer_id},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["total_points"] == 0


class TestConsumerProfile:
    """W12-001: 消费者档案与会员等级"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("payload", [{"phone": "13800138000"}, {"nickname": "王小明"}])
    async def test_admin_cannot_create_consumer_pii_without_subject_consent(self, client, setup_tenant, payload):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/consumers",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "consumer_consent_required"

    @pytest.mark.anyio
    async def test_anonymous_profile_requires_live_login_session(self, client, setup_tenant):
        tid, _headers = setup_tenant
        sessionless_token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")

        resp = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers={"Authorization": f"Bearer {sessionless_token}"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Live login session required for member changes"

    @pytest.mark.anyio
    async def test_operator_with_canonical_permission_can_create_anonymous_profile(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        tenant_id, _headers = setup_tenant
        headers, _account_id = await _login_member_role(
            client,
            db_session,
            tenant_id,
            role_name="operator",
        )
        before = await db_session.scalar(
            select(func.count()).select_from(ConsumerProfile).where(ConsumerProfile.tenant_id == uuid.UUID(tenant_id))
        )

        response = await client.post("/api/v1/members/consumers", json={}, headers=headers)

        after = await db_session.scalar(
            select(func.count()).select_from(ConsumerProfile).where(ConsumerProfile.tenant_id == uuid.UUID(tenant_id))
        )
        assert response.status_code == 201
        assert response.json()["nickname"] is None
        assert after == before + 1

    @pytest.mark.anyio
    async def test_viewer_cannot_create_anonymous_profile(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        tenant_id, _headers = setup_tenant
        headers, _account_id = await _login_member_role(
            client,
            db_session,
            tenant_id,
            role_name="viewer",
        )
        before = await db_session.scalar(select(func.count()).select_from(ConsumerProfile))

        response = await client.post("/api/v1/members/consumers", json={}, headers=headers)

        assert response.status_code == 403
        assert await db_session.scalar(select(func.count()).select_from(ConsumerProfile)) == before

    @pytest.mark.anyio
    async def test_revoked_session_cannot_create_anonymous_profile(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        _tenant_id, headers = setup_tenant
        payload = decode_token(headers["Authorization"].removeprefix("Bearer "))
        session = await db_session.get(AuthSession, uuid.UUID(payload["sid"]))
        assert session is not None
        session.revoked_at = datetime.now(UTC)
        await db_session.commit()
        before = await db_session.scalar(select(func.count()).select_from(ConsumerProfile))

        response = await client.post("/api/v1/members/consumers", json={}, headers=headers)

        assert response.status_code == 403
        assert await db_session.scalar(select(func.count()).select_from(ConsumerProfile)) == before

    @pytest.mark.anyio
    async def test_revoked_consumer_permission_cannot_create_anonymous_profile(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        tenant_id, headers = setup_tenant
        tenant_uuid = uuid.UUID(tenant_id)
        admin_role_id = await db_session.scalar(
            select(Role.id).where(Role.tenant_id == tenant_uuid, Role.name == "admin")
        )
        permission_id = await db_session.scalar(
            select(Permission.id).where(
                Permission.tenant_id == tenant_uuid,
                Permission.code == "consumer:detail",
            )
        )
        assert admin_role_id is not None and permission_id is not None
        await db_session.execute(
            delete(role_permissions).where(
                role_permissions.c.tenant_id == tenant_uuid,
                role_permissions.c.role_id == admin_role_id,
                role_permissions.c.permission_id == permission_id,
            )
        )
        await db_session.commit()
        before = await db_session.scalar(select(func.count()).select_from(ConsumerProfile))

        response = await client.post("/api/v1/members/consumers", json={}, headers=headers)

        assert response.status_code == 403
        assert await db_session.scalar(select(func.count()).select_from(ConsumerProfile)) == before

    @pytest.mark.anyio
    async def test_anonymous_profile_creation_cannot_write_another_tenant(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        source_tenant_id, headers = setup_tenant
        other = await client.post(
            "/api/v1/tenants",
            json={
                "name": "隔离会员租户",
                "admin_email": f"isolated-{uuid.uuid4().hex[:8]}@test.com",
                "admin_name": "Isolated Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert other.status_code == 201
        other_tenant_id = uuid.UUID(other.json()["id"])

        response = await client.post("/api/v1/members/consumers", json={}, headers=headers)

        assert response.status_code == 201
        assert (
            await db_session.scalar(
                select(func.count()).select_from(ConsumerProfile).where(ConsumerProfile.tenant_id == other_tenant_id)
            )
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(ConsumerProfile)
                .where(ConsumerProfile.tenant_id == uuid.UUID(source_tenant_id))
            )
            == 1
        )

    @pytest.mark.anyio
    async def test_get_consumer_profile(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        consumer_id = consumer.json()["id"]

        resp = await client.get(
            f"/api/v1/members/consumers/{consumer_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "total_points" in resp.json()
        assert "recent_transactions" in resp.json()

    @pytest.mark.anyio
    async def test_rejected_admin_pii_is_not_searchable(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={"phone": "13800138000", "nickname": "王小明"},
            headers=headers,
        )
        assert consumer.status_code == 403

        by_phone = await client.get(
            "/api/v1/members/consumers/search",
            params={"keyword": "13800138000", "lookup_type": "phone"},
            headers=headers,
        )
        assert by_phone.status_code == 200
        assert by_phone.json()["items"] == []

        by_name = await client.get(
            "/api/v1/members/consumers/search",
            params={"keyword": "小明", "lookup_type": "nickname"},
            headers=headers,
        )
        assert by_name.status_code == 200
        assert by_name.json()["items"] == []


class TestPointsAward:
    """W12-003: 积分发放"""

    @pytest.mark.anyio
    async def test_award_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 100, "reason": "扫码奖励"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["amount"] == 100
        assert resp.json()["balance_after"] == 100

    @pytest.mark.anyio
    async def test_award_points_accumulates(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 50, "reason": "首次扫码"},
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 30, "reason": "重复扫码"},
            headers=headers,
        )
        assert resp.json()["balance_after"] == 80

    @pytest.mark.anyio
    async def test_member_level_upgrade(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        # 发放 1000 积分 → silver
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 1000, "reason": "大批奖励"},
            headers=headers,
        )

        profile = await client.get(
            f"/api/v1/members/consumers/{cid}",
            headers=headers,
        )
        assert profile.json()["member_level"] == "silver"


class TestPointsSpend:
    """W12-004: 积分消费"""

    @pytest.mark.anyio
    async def test_spend_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 200, "reason": "初始积分"},
            headers=headers,
        )

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 50, "reason": "兑换优惠券"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["amount"] == -50
        assert resp.json()["balance_after"] == 150

    @pytest.mark.anyio
    async def test_spend_insufficient_points(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 100, "reason": "余额不足"},
            headers=headers,
        )
        assert resp.status_code == 400


class TestMemberOverview:
    @pytest.mark.anyio
    async def test_overview_counts_rules_products_points_and_redemptions(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post("/api/v1/members/point-rules", json={"rule_type": "scan", "points": 10}, headers=headers)
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "兑换券", "points_cost": 20, "stock": 2, "enabled": True},
            headers=headers,
        )
        await client.post(
            "/api/v1/members/points/award", json={"consumer_id": cid, "points": 100, "reason": "测试"}, headers=headers
        )
        token = await create_scan_context(db_session, tid, consumer_id=cid)
        exchange = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange.status_code == 200

        overview = await client.get("/api/v1/members/overview", headers=headers)
        assert overview.status_code == 200
        data = overview.json()
        assert data["enabled_rules"] == 1
        assert data["active_products"] == 1
        assert data["points_awarded_7d"] == 100
        assert data["points_spent_7d"] == 20
        assert data["redemptions_7d"] == 1


class TestPointRules:
    """W12-003: 积分规则配置"""

    @pytest.mark.anyio
    async def test_create_point_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/point-rules",
            json={"rule_type": "scan", "points": 10},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["rule_type"] == "scan"
        assert resp.json()["points"] == 10

    @pytest.mark.anyio
    async def test_list_point_rules(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/members/point-rules",
            json={"rule_type": "first_scan", "points": 50},
            headers=headers,
        )
        resp = await client.get("/api/v1/members/point-rules", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["items"]) >= 1


class TestTransactions:
    """W12-005: 积分流水查询"""

    @pytest.mark.anyio
    async def test_list_transactions(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 100, "reason": "扫码"},
            headers=headers,
        )
        await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 30, "reason": "兑换"},
            headers=headers,
        )

        resp = await client.get(
            f"/api/v1/members/consumers/{cid}/transactions",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["items"][0]["txn_type"] == "spending"
        assert data["items"][1]["txn_type"] == "earning"


class TestPointProducts:
    @pytest.mark.anyio
    async def test_product_crud_returns_validity_and_limits(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/point-products",
            json={
                "name": "积分礼品",
                "points_cost": 30,
                "stock": 5,
                "per_consumer_limit": 2,
                "sort_order": 3,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["per_consumer_limit"] == 2
        assert data["sort_order"] == 3

        updated = await client.put(
            f"/api/v1/members/point-products/{data['id']}",
            json={"enabled": False, "per_consumer_limit": 0},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert updated.json()["per_consumer_limit"] == 0

    @pytest.mark.anyio
    async def test_h5_exchange_success_writes_redemption_and_refreshes_products(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post(
            "/api/v1/members/points/award", json={"consumer_id": cid, "points": 80, "reason": "初始"}, headers=headers
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "积分券", "points_cost": 50, "stock": 1, "per_consumer_limit": 1},
            headers=headers,
        )
        token = await create_scan_context(db_session, tid, consumer_id=cid)

        products = await client.get(
            "/api/v1/consumers/points/products",
            params={"consumer_id": cid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert products.status_code == 200
        assert products.json()["items"][0]["can_exchange"] is True

        exchange = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange.status_code == 200
        assert exchange.json()["points_spent"] == 50
        assert exchange.json()["balance_after"] == 30

        redemptions = await client.get("/api/v1/members/point-redemptions", headers=headers)
        assert redemptions.status_code == 200
        assert redemptions.json()["total"] == 1
        assert redemptions.json()["items"][0]["product_name"] == "积分券"

        blocked = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert blocked.status_code == 400

    @pytest.mark.anyio
    async def test_expired_plan_blocks_h5_exchange_but_keeps_points_readable(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 80, "reason": "到期前积分"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "到期保护券", "points_cost": 50, "stock": 1},
            headers=headers,
        )
        token = await create_scan_context(db_session, tid, consumer_id=cid)
        tenant = await db_session.get(Tenant, uuid.UUID(tid))
        assert tenant is not None
        tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()

        exchange = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        points = await client.get(
            "/api/v1/consumers/points/me",
            params={"consumer_id": cid},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert exchange.status_code == 403
        assert exchange.json()["code"] == "TENANT_PLAN_EXPIRED"
        assert points.status_code == 200
        assert points.json()["total_points"] == 80


@pytest.mark.anyio
async def test_expired_plan_blocks_consumer_lead_capture(client: AsyncClient, setup_tenant, db_session: AsyncSession):
    tid, _headers = setup_tenant
    tenant = await db_session.get(Tenant, uuid.UUID(tid))
    assert tenant is not None
    tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    public_id = f"EXPIRED{uuid.uuid4().hex[:8]}"
    ip_address = "203.0.113.9"
    token = create_scan_token(
        public_id,
        compute_ip_hash(ip_address),
        tenant_id=tid,
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )

    with (
        patch("app.api.v1.consumers.get_client_ip", return_value=ip_address),
        patch("app.api.v1.consumers.enforce_public_consumer_admission"),
    ):
        response = await client.post(
            "/api/v1/consumers/lead-capture",
            json={
                "consent_id": str(uuid.uuid4()),
                "idempotency_key": f"lead-{uuid.uuid4()}",
                "phone": "13800138000",
                "region": "华东",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_PLAN_EXPIRED"


@pytest.mark.anyio
async def test_lead_capture_rejects_scan_token_without_exact_scan_time_before_mutation(
    client: AsyncClient,
    setup_tenant,
):
    tid, _headers = setup_tenant
    token = create_scan_token(
        "LEGACY-SCAN",
        compute_ip_hash("203.0.113.9"),
        tenant_id=tid,
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )

    with patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.9"):
        response = await client.post(
            "/api/v1/consumers/lead-capture",
            json={
                "consent_id": str(uuid.uuid4()),
                "idempotency_key": f"lead-{uuid.uuid4()}",
                "phone": "13800138000",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_scan_authority"


@pytest.mark.anyio
async def test_sqlite_consent_lead_withdraw_matches_authoritative_public_journey(
    client: AsyncClient,
    setup_tenant,
    db_session: AsyncSession,
):
    tenant_id = uuid.UUID(setup_tenant[0])
    public_id = f"CONSENT{uuid.uuid4().hex[:8]}"
    visitor_id = uuid.uuid4().hex
    scan_event_id = uuid.uuid4()
    scan_time = datetime.now(UTC)
    batch_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    privacy_policy_id = uuid.uuid4()
    content = "仅用于联系您处理本次咨询。"
    digest = hashlib.sha256(content.encode()).hexdigest()
    db_session.add(
        CodeBatch(
            id=batch_id,
            tenant_id=tenant_id,
            product_id=uuid.uuid4(),
            sku_id=uuid.uuid4(),
            production_batch_id=uuid.uuid4(),
            batch_code=f"B-{public_id}",
            quantity=1,
            status="activated",
            code_type="single",
            created_by=uuid.uuid4(),
        )
    )
    db_session.add(
        CodeItem(
            tenant_id=tenant_id,
            code_batch_id=batch_id,
            public_id=public_id,
            status=CodeItemStatus.activated,
            code_type="single",
        )
    )
    db_session.add(AnonymousVisitor(tenant_id=tenant_id, visitor_id=visitor_id))
    db_session.add(
        ScanEvent(
            id=scan_event_id,
            tenant_id=tenant_id,
            public_id=public_id,
            scan_time=scan_time,
            visitor_id=visitor_id,
            is_valid_visit=True,
        )
    )
    db_session.add(
        ConsumerConsentPolicy(
            id=policy_id,
            tenant_id=tenant_id,
            purpose="lead_capture",
            consent_type="marketing",
            policy_version="v1",
            policy_digest=digest,
            policy_title="联系授权",
            policy_content=content,
            effective_at=scan_time - timedelta(seconds=1),
        )
    )
    db_session.add(ConsumerConsentPolicyCurrent(tenant_id=tenant_id, purpose="lead_capture", policy_id=policy_id))
    db_session.add(
        ConsumerConsentPolicy(
            id=privacy_policy_id,
            tenant_id=tenant_id,
            purpose="privacy_policy",
            consent_type="privacy",
            policy_version="v1",
            policy_digest=digest,
            policy_title="隐私政策",
            policy_content=content,
            effective_at=scan_time - timedelta(seconds=1),
        )
    )
    db_session.add(
        ConsumerConsentPolicyCurrent(
            tenant_id=tenant_id,
            purpose="privacy_policy",
            policy_id=privacy_policy_id,
        )
    )
    await db_session.commit()
    token = create_scan_token(
        public_id,
        compute_ip_hash("203.0.113.9"),
        tenant_id=str(tenant_id),
        scan_event_id=str(scan_event_id),
        scan_time=scan_time.isoformat(),
        visitor_id=visitor_id,
    )

    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consents.enforce_public_consumer_admission"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission"),
    ):
        privacy_grant = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "purpose": "privacy_policy",
                "policy_version": "v1",
                "policy_digest": digest,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )
        policy = await client.get(
            "/api/v1/public/consents/policy",
            params={"purpose": "lead_capture"},
            headers={"Authorization": f"Bearer {token}"},
        )
        grant = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "purpose": "lead_capture",
                "policy_version": "v1",
                "policy_digest": digest,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )
        lead = await client.post(
            "/api/v1/consumers/lead-capture",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "consent_id": grant.json()["consent_id"],
                "idempotency_key": f"lead-{uuid.uuid4()}",
                "phone": "13800138000",
                "name": "测试用户",
                "region": "华东",
                "intention": "咨询",
            },
        )
        bound_token = lead.json()["scan_token"]
        restored_anonymous_receipt = await client.get(
            f"/api/v1/public/consents/{privacy_grant.json()['consent_id']}/status",
            headers={"Authorization": f"Bearer {bound_token}"},
        )
        assert restored_anonymous_receipt.status_code == 200
        assert restored_anonymous_receipt.json()["consent_id"] == privacy_grant.json()["consent_id"]
        coalesced_grant = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={
                "purpose": "lead_capture",
                "policy_version": "v1",
                "policy_digest": digest,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )
        withdraw_key = f"withdraw-{uuid.uuid4()}"
        initial_withdraw = await client.post(
            f"/api/v1/public/consents/{grant.json()['consent_id']}/withdraw",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={"idempotency_key": withdraw_key},
        )
        replacement_grant = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={
                "purpose": "lead_capture",
                "policy_version": "v1",
                "policy_digest": digest,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )
        replacement_lead = await client.post(
            "/api/v1/consumers/lead-capture",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={
                "consent_id": replacement_grant.json()["consent_id"],
                "idempotency_key": f"lead-{uuid.uuid4()}",
                "phone": "13800138000",
                "name": "新授权用户",
                "region": "华南",
                "intention": "复购",
            },
        )
        other_consumer_id = uuid.uuid4()
        other_phone = "13700137000"
        other_ciphertext, other_nonce, other_key_id = encrypt_consumer_phone(tenant_id, other_consumer_id, other_phone)
        db_session.add(
            ConsumerProfile(
                id=other_consumer_id,
                tenant_id=tenant_id,
                phone_hash=hash_phone(other_phone),
                phone_ciphertext=other_ciphertext,
                phone_nonce=other_nonce,
                phone_key_id=other_key_id,
            )
        )
        await db_session.commit()
        cross_profile_idempotency = f"lead-{uuid.uuid4()}"
        cross_profile_conflict = await client.post(
            "/api/v1/consumers/lead-capture",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={
                "consent_id": replacement_grant.json()["consent_id"],
                "idempotency_key": cross_profile_idempotency,
                "phone": other_phone,
                "name": "冲突用户",
            },
        )
        bound_profile = await db_session.get(ConsumerProfile, uuid.UUID(lead.json()["consumer_id"]))
        await db_session.refresh(bound_profile)
        bound_profile_phone_hash = bound_profile.phone_hash
        cross_profile_action = await db_session.scalar(
            select(ConsumerConsentAction).where(
                ConsumerConsentAction.tenant_id == tenant_id,
                ConsumerConsentAction.action == "lead_capture",
                ConsumerConsentAction.idempotency_key == cross_profile_idempotency,
            )
        )
        stale_withdraw_replay = await client.post(
            f"/api/v1/public/consents/{grant.json()['consent_id']}/withdraw",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={"idempotency_key": withdraw_key},
        )
        active_me = await client.get(
            "/api/v1/consumers/me",
            headers={"Authorization": f"Bearer {bound_token}"},
        )
        profile = await db_session.get(ConsumerProfile, uuid.UUID(lead.json()["consumer_id"]))
        stale_withdraw_profile = (
            profile.lead_consent_id,
            profile.lead_contact_suppressed,
            profile.phone_hash,
            profile.phone_ciphertext,
            profile.phone_nonce,
            profile.phone_key_id,
            profile.nickname,
            profile.extra_data,
        )
        current_withdraw = await client.post(
            f"/api/v1/public/consents/{replacement_grant.json()['consent_id']}/withdraw",
            headers={"Authorization": f"Bearer {bound_token}"},
            json={"idempotency_key": f"withdraw-{uuid.uuid4()}"},
        )
        suppressed_me = await client.get(
            "/api/v1/consumers/me",
            headers={"Authorization": f"Bearer {bound_token}"},
        )
        current_withdraw_profile = (
            profile.lead_consent_id,
            profile.lead_contact_suppressed,
            profile.phone_hash,
            profile.phone_ciphertext,
            profile.phone_nonce,
            profile.phone_key_id,
            profile.nickname,
            profile.extra_data,
        )

    assert policy.status_code == 200
    assert grant.status_code == 201
    assert lead.status_code == 201
    assert coalesced_grant.status_code == 201
    assert coalesced_grant.json()["consent_id"] == grant.json()["consent_id"]
    assert coalesced_grant.json()["replayed"] is True
    assert initial_withdraw.status_code == 200
    assert initial_withdraw.json()["contact_suppressed"] is True
    assert replacement_grant.status_code == 201
    assert replacement_lead.status_code == 201
    assert replacement_lead.json()["consumer_id"] == lead.json()["consumer_id"]
    assert cross_profile_conflict.status_code == 409
    assert cross_profile_conflict.json()["detail"] == "consent_request_conflict"
    assert bound_profile_phone_hash == hash_phone("13800138000")
    assert cross_profile_action is None
    assert stale_withdraw_replay.status_code == 200
    assert stale_withdraw_replay.json()["contact_suppressed"] is False
    assert active_me.status_code == 200
    assert active_me.json()["nickname"] == "新授权用户"
    assert stale_withdraw_profile[0] == uuid.UUID(replacement_grant.json()["consent_id"])
    assert stale_withdraw_profile[1] is False
    assert stale_withdraw_profile[2] is not None
    assert stale_withdraw_profile[3] is not None
    assert stale_withdraw_profile[4] is not None
    assert stale_withdraw_profile[5] is not None
    assert stale_withdraw_profile[6] == "新授权用户"
    assert stale_withdraw_profile[7]["region"] == "华南"
    assert stale_withdraw_profile[7]["intention"] == "复购"
    assert current_withdraw.status_code == 200
    assert current_withdraw.json()["contact_suppressed"] is True
    assert suppressed_me.status_code == 200
    assert suppressed_me.json()["nickname"] is None
    assert current_withdraw_profile[0] == uuid.UUID(replacement_grant.json()["consent_id"])
    assert current_withdraw_profile[1] is True
    assert current_withdraw_profile[2] is None
    assert current_withdraw_profile[3] is None
    assert current_withdraw_profile[4] is None
    assert current_withdraw_profile[5] is None
    assert current_withdraw_profile[6] is None
    assert "region" not in current_withdraw_profile[7]
    assert "intention" not in current_withdraw_profile[7]


class TestPointsValidation:
    """积分值正数校验"""

    @pytest.mark.anyio
    async def test_award_rejects_negative_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": -100, "reason": "负数积分"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_award_rejects_zero_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 0, "reason": "零积分"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_spend_rejects_negative_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": -50, "reason": "负数消费"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_award_rejects_oversized_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 1_000_001, "reason": "超大积分"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestConsumerIdentityBinding:
    """scan_token consumer_id 绑定与所有权验证"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("surface", ["me", "points", "transactions", "products", "exchange"])
    async def test_consumer_surfaces_accept_same_ip_bound_token(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
        surface: str,
    ):
        tenant_id, admin_headers = setup_tenant
        consumer_id = (await client.post("/api/v1/members/consumers", json={}, headers=admin_headers)).json()["id"]
        token = await create_scan_context(db_session, tenant_id, consumer_id=consumer_id)
        scan_headers = {"Authorization": f"Bearer {token}"}

        if surface == "me":
            response = await client.get(
                "/api/v1/consumers/me", params={"consumer_id": consumer_id}, headers=scan_headers
            )
        elif surface == "points":
            response = await client.get(
                "/api/v1/consumers/points/me", params={"consumer_id": consumer_id}, headers=scan_headers
            )
        elif surface == "transactions":
            response = await client.get(
                "/api/v1/consumers/points/transactions",
                params={"consumer_id": consumer_id},
                headers=scan_headers,
            )
        elif surface == "products":
            response = await client.get(
                "/api/v1/consumers/points/products",
                params={"consumer_id": consumer_id},
                headers=scan_headers,
            )
        else:
            await client.post(
                "/api/v1/members/points/award",
                json={"consumer_id": consumer_id, "points": 100, "reason": "IP-bound exchange"},
                headers=admin_headers,
            )
            product = await client.post(
                "/api/v1/members/point-products",
                json={"name": "IP-bound product", "points_cost": 50, "stock": 1, "enabled": True},
                headers=admin_headers,
            )
            response = await client.post(
                "/api/v1/consumers/points/exchanges",
                json={"consumer_id": consumer_id, "product_id": product.json()["id"]},
                headers=scan_headers,
            )

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_exchange_with_bound_consumer_id(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 100, "reason": "初始积分"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "绑定测试券", "points_cost": 50, "stock": 10, "enabled": True},
            headers=headers,
        )

        # token 绑定到 consumer c1 → c1 兑换成功
        token = await create_scan_context(db_session, tid, consumer_id=cid)
        resp = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["points_spent"] == 50

    @pytest.mark.anyio
    async def test_exchange_rejects_mismatched_consumer_id(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        c1 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        c2 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid1 = c1.json()["id"]
        cid2 = c2.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid2, "points": 100, "reason": "初始积分"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "冒用测试券", "points_cost": 50, "stock": 10, "enabled": True},
            headers=headers,
        )

        # token 绑定到 c1，但尝试以 c2 身份兑换 → 403
        token = await create_scan_context(db_session, tid, consumer_id=cid1)
        resp = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid2, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "mismatch" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_private_routes_reject_unbound_scan_token(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        token = await create_scan_context(db_session, tid)
        auth = {"Authorization": f"Bearer {token}"}

        responses = [
            await client.get("/api/v1/consumers/me", params={"consumer_id": cid}, headers=auth),
            await client.get("/api/v1/consumers/points/me", params={"consumer_id": cid}, headers=auth),
            await client.get("/api/v1/consumers/points/transactions", params={"consumer_id": cid}, headers=auth),
            await client.get("/api/v1/consumers/points/products", params={"consumer_id": cid}, headers=auth),
        ]
        assert [response.status_code for response in responses] == [401, 401, 401, 401]

    @pytest.mark.anyio
    async def test_private_reads_reject_same_tenant_other_consumer(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        c1 = (await client.post("/api/v1/members/consumers", json={}, headers=headers)).json()["id"]
        c2 = (await client.post("/api/v1/members/consumers", json={}, headers=headers)).json()["id"]
        token = await create_scan_context(db_session, tid, consumer_id=c1)
        auth = {"Authorization": f"Bearer {token}"}

        responses = [
            await client.get("/api/v1/consumers/me", params={"consumer_id": c2}, headers=auth),
            await client.get("/api/v1/consumers/points/me", params={"consumer_id": c2}, headers=auth),
            await client.get("/api/v1/consumers/points/transactions", params={"consumer_id": c2}, headers=auth),
            await client.get("/api/v1/consumers/points/products", params={"consumer_id": c2}, headers=auth),
        ]
        assert [response.status_code for response in responses] == [403, 403, 403, 403]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "method,path,request_kwargs,service_name",
        [
            pytest.param(
                "GET",
                "/api/v1/consumers/me",
                {"params": {"consumer_id": str(uuid.uuid4())}},
                "set_session_tenant_context",
                id="me",
            ),
            pytest.param(
                "GET",
                "/api/v1/consumers/points/me",
                {"params": {"consumer_id": str(uuid.uuid4())}},
                "get_consumer_profile",
                id="points-me",
            ),
            pytest.param(
                "GET",
                "/api/v1/consumers/points/transactions",
                {"params": {"consumer_id": str(uuid.uuid4())}},
                "list_point_transactions",
                id="transactions",
            ),
            pytest.param(
                "GET",
                "/api/v1/consumers/points/products",
                {"params": {"consumer_id": str(uuid.uuid4())}},
                "list_consumer_point_products",
                id="products",
            ),
            pytest.param(
                "POST",
                "/api/v1/consumers/points/exchanges",
                {"json": {"consumer_id": str(uuid.uuid4()), "product_id": str(uuid.uuid4())}},
                "exchange_product",
                id="exchange",
            ),
            pytest.param(
                "POST",
                "/api/v1/consumers/lead-capture",
                {
                    "json": {
                        "consent_id": str(uuid.uuid4()),
                        "idempotency_key": f"lead-{uuid.uuid4()}",
                        "phone": "13800138000",
                    }
                },
                "capture_consumer_lead_authority",
                id="lead-capture",
            ),
            pytest.param(
                "POST",
                "/api/v1/public/leads",
                {
                    "json": {
                        "consent_id": str(uuid.uuid4()),
                        "idempotency_key": f"lead-alias-{uuid.uuid4()}",
                        "phone": "13800138000",
                    }
                },
                "capture_consumer_lead_authority",
                id="lead-capture-alias",
            ),
        ],
    )
    async def test_consumer_routes_reject_wrong_ip_before_database_or_service(
        self,
        client: AsyncClient,
        method: str,
        path: str,
        request_kwargs: dict,
        service_name: str,
    ):
        database_calls = 0

        async def denied_consumer_db():
            nonlocal database_calls
            database_calls += 1
            raise AssertionError("consumer database dependency ran before scan token IP validation")
            yield  # pragma: no cover - keep this an async generator dependency

        token = create_scan_token(
            "IP-BOUND-CODE",
            compute_ip_hash("203.0.113.10"),
            tenant_id=str(uuid.uuid4()),
            consumer_id=str(uuid.uuid4()),
            scan_event_id=str(uuid.uuid4()),
            scan_time=datetime.now(UTC).isoformat(),
            visitor_id=str(uuid.uuid4()),
        )
        original_consumer_db = app.dependency_overrides[get_db_for_consumer]
        app.dependency_overrides[get_db_for_consumer] = denied_consumer_db
        service = AsyncMock()
        try:
            with (
                patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.11"),
                patch(f"app.api.v1.consumers.{service_name}", new=service),
            ):
                response = await client.request(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                    **request_kwargs,
                )
        finally:
            app.dependency_overrides[get_db_for_consumer] = original_consumer_db

        assert response.status_code == 401
        assert response.json()["detail"] in {"invalid token", "invalid_token"}
        assert database_calls == 0
        service.assert_not_awaited()


class TestConcurrentOperations:
    """并发操作安全性

    注意: SQLite 下 with_for_update() 是 no-op，行锁仅在 PostgreSQL 下生效。
    测试验证 API 层面的积分累加流程正确性。真正的并发安全在 PostgreSQL 生产环境中保障。
    """

    @pytest.mark.anyio
    async def test_rapid_award_preserves_balance(self, client: AsyncClient, setup_tenant):
        """快速连续发放积分应正确累加，不丢失"""
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]

        # 连续发放 10 次，每次 10 积分
        successes = 0
        for _ in range(10):
            resp = await client.post(
                "/api/v1/members/points/award",
                json={"consumer_id": cid, "points": 10, "reason": "连续发放测试"},
                headers=headers,
            )
            if resp.status_code == 200:
                successes += 1

        # 验证最终余额
        profile = await client.get(
            f"/api/v1/members/consumers/{cid}",
            headers=headers,
        )
        assert profile.json()["total_points"] == successes * 10
