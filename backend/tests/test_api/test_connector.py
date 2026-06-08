"""外部权益连接器 API 集成测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.connector import BenefitDelivery
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}




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
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestCouponPool:
    """券码池导入与发放"""

    @pytest.mark.anyio
    async def test_import_coupon_codes(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={
                "name": "测试优惠券池",
                "codes": ["COUPON001", "COUPON002", "COUPON003"],
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试优惠券池"
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


class TestConnectorCRUD:
    """连接器 CRUD 操作"""

    @pytest.mark.anyio
    async def test_list_connector_types(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.get("/api/v1/connectors/connectors/types", headers=headers)
        assert resp.status_code == 200
        types = resp.json()["types"]
        assert "generic_http" in types
        assert "coupon_pool" in types

    @pytest.mark.anyio
    async def test_create_generic_http_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "测试 HTTP 连接器",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试 HTTP 连接器"
        assert data["connector_type"] == "generic_http"
        assert data["enabled"] is True

    @pytest.mark.anyio
    async def test_create_coupon_pool_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        # 先创建券码池
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "关联测试池", "codes": ["C001"]},
            headers=headers,
        )
        pool_id = pool_resp.json()["id"]

        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "券码池连接器",
                "connector_type": "coupon_pool",
                "config": {"pool_id": pool_id},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["connector_type"] == "coupon_pool"

    @pytest.mark.anyio
    async def test_create_connector_invalid_config(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "无效配置",
                "connector_type": "generic_http",
                "config": {},
            },
            headers=headers,
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_list_connectors(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "列表测试", "connector_type": "generic_http", "config": {"api_url": "https://api.example.com"}},
            headers=headers,
        )

        resp = await client.get("/api/v1/connectors/connectors", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.anyio
    async def test_update_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "更新测试", "connector_type": "generic_http", "config": {"api_url": "https://api.example.com"}},
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/connectors/connectors/{conn_id}",
            json={"name": "已更新"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "已更新"

    @pytest.mark.anyio
    async def test_test_connection(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "连接测试", "connector_type": "generic_http", "config": {"api_url": "https://api.example.com"}},
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/connectors/connectors/{conn_id}/test",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestConnectorSecrets:
    """连接器凭证管理"""

    @pytest.mark.anyio
    async def test_create_with_secrets(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "带凭证连接器",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
                "secrets": {"api_key": "sk_live_12345678", "callback_secret": "cb-secret"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        # 凭证应脱敏显示
        assert "secrets" in data
        assert "sk_" in data["secrets"]["api_key"]
        assert "12345678" not in str(data["secrets"]["api_key"])

    @pytest.mark.anyio
    async def test_update_secrets(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={"name": "更新凭证", "connector_type": "generic_http", "config": {"api_url": "https://api.example.com"}},
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/connectors/connectors/{conn_id}",
            json={"secrets": {"api_key": "new-key"}},
            headers=headers,
        )
        assert resp.status_code == 200


class TestBenefitDeliveries:
    """权益发放记录详情与重试"""

    @pytest.mark.anyio
    async def test_get_delivery_detail(self, client: AsyncClient, db_session: AsyncSession, setup_tenant):
        tenant_id, headers = setup_tenant
        delivery_id = uuid.uuid4()
        db_session.add(
            BenefitDelivery(
                id=delivery_id,
                tenant_id=uuid.UUID(tenant_id),
                connector_id=uuid.uuid4(),
                consumer_id="consumer-detail-001",
                benefit_type="coupon",
                benefit_config={"amount": 10},
                status="failed",
                retry_count=2,
                max_retries=5,
                external_data={"error": "invalid receiver"},
            )
        )
        await db_session.flush()

        resp = await client.get(f"/api/v1/connectors/deliveries/{delivery_id}", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(delivery_id)
        assert data["consumer_id"] == "consumer-detail-001"
        assert data["status"] == "failed"
        assert data["retry_count"] == 2
        assert data["max_retries"] == 5
        assert data["external_data"] == {"error": "invalid receiver"}
        assert data["created_at"]
        assert data["updated_at"]

    @pytest.mark.anyio
    async def test_retry_failed_delivery_runs_again(self, client: AsyncClient, db_session: AsyncSession, setup_tenant):
        tenant_id, headers = setup_tenant
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "重试券码池", "codes": ["RETRY001"]},
            headers=headers,
        )
        connector_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "重试券码池连接器",
                "connector_type": "coupon_pool",
                "config": {"pool_id": pool_resp.json()["id"]},
            },
            headers=headers,
        )
        connector_id = connector_resp.json()["id"]
        delivery_id = uuid.uuid4()
        db_session.add(
            BenefitDelivery(
                id=delivery_id,
                tenant_id=uuid.UUID(tenant_id),
                connector_id=uuid.UUID(connector_id),
                consumer_id="consumer-retry-001",
                benefit_type="coupon",
                benefit_config={"benefit_type": "platform_coupon"},
                status="failed",
                retry_count=1,
                max_retries=5,
                external_data={"error": "temporary failure"},
            )
        )
        await db_session.flush()

        resp = await client.post(f"/api/v1/connectors/deliveries/{delivery_id}/retry", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] != str(delivery_id)
        assert data["consumer_id"] == "consumer-retry-001"
        assert data["status"] == "success"
        assert data["external_data"]["code"] == "RETRY001"
