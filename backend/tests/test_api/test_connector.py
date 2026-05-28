"""W18: 外部权益连接器测试"""

from collections.abc import AsyncGenerator

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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "连接器测试租户",
            "admin_email": "connector@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestCouponPool:
    """W18-001: 外部券码池导入与发放"""

    @pytest.mark.anyio
    async def test_import_coupon_codes(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={
                "name": "有赞优惠券池",
                "codes": ["COUPON001", "COUPON002", "COUPON003"],
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "有赞优惠券池"
        assert data["total_codes"] == 3
        assert data["remaining"] == 3

    @pytest.mark.anyio
    async def test_distribute_coupon(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={
                "name": "发放测试池",
                "codes": ["DIS001", "DIS002"],
            },
            headers=headers,
        )
        pool_id = pool_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/connectors/coupon-pools/{pool_id}/distribute",
            json={"consumer_id": "consumer-001"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "DIS001"
        assert data["consumer_id"] == "consumer-001"


class TestConnectorProtocol:
    """W18-002: 连接器接口协议"""

    @pytest.mark.anyio
    async def test_create_connector(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "有赞连接器",
                "connector_type": "youzan",
                "config": {"api_key": "test_key", "shop_id": "12345"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "有赞连接器"

    @pytest.mark.anyio
    async def test_test_connection(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "测试连接器",
                "connector_type": "mock",
                "config": {},
            },
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/connectors/connectors/{conn_id}/test",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestConnectorConfig:
    """W18-003: 连接器配置管理"""

    @pytest.mark.anyio
    async def test_list_connectors(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "列表连接器", "connector_type": "mock", "config": {}},
            headers=headers,
        )

        resp = await client.get("/api/v1/connectors/connectors", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.anyio
    async def test_update_connector(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "更新测试", "connector_type": "mock", "config": {}},
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/connectors/connectors/{conn_id}",
            json={"name": "已更新", "config": {"new_key": "new_value"}},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "已更新"
