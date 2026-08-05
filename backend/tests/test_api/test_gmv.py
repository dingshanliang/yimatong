"""W16: 外部成交导入与 GMV 归因测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.gmv import ExternalOrder, GmvAttribution
from app.utils.crypto import hash_phone
from app.utils.security import create_access_token
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
            "name": "GMV测试租户",
            "admin_email": "gmv@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestExternalOrderImport:
    """W16-001: 外部订单导入"""

    @pytest.mark.anyio
    async def test_import_orders(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/gmv/orders/import",
            json={
                "orders": [
                    {
                        "external_id": "ORD-001",
                        "amount": 99.9,
                        "phone": "13900139001",
                        "product_name": "脐橙",
                        "order_time": "2026-05-28T10:00:00Z",
                    },
                    {
                        "external_id": "ORD-002",
                        "amount": 199.0,
                        "phone": "13900139002",
                        "product_name": "蜂蜜",
                        "order_time": "2026-05-28T11:00:00Z",
                    },
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # yimatong-zgb1.13：import_orders 返回逐行结果（created/skipped_duplicates/failed/errors）
        assert data["created"] == 2

    @pytest.mark.anyio
    async def test_list_orders(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/gmv/orders/import",
            json={
                "orders": [
                    {
                        "external_id": "ORD-LIST",
                        "amount": 50.0,
                        "phone": "13900139003",
                        "product_name": "测试商品",
                        "order_time": "2026-05-28T10:00:00Z",
                    },
                ],
            },
            headers=headers,
        )

        resp = await client.get("/api/v1/gmv/orders", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestGmvDashboard:
    """W16-003: GMV 归因看板"""

    @pytest.mark.anyio
    async def test_gmv_summary(self, client: AsyncClient, setup_tenant, db_session: AsyncSession):
        tid, headers = setup_tenant

        # 手动插入归因记录
        from app.models.scan import ScanEvent

        scan_event = ScanEvent(
            tenant_id=UUID(tid),
            public_id="GMV_CODE",
            scan_time=datetime.now(UTC),
            ip_hash="ip_gmv",
        )
        db_session.add(scan_event)
        await db_session.flush()

        order = ExternalOrder(
            tenant_id=UUID(tid),
            external_id="GMV-ORD",
            amount=299.0,
            phone_hash=hash_phone("13800138001"),
            product_name="GMV商品",
            order_time=datetime.now(UTC),
            matched=True,
        )
        db_session.add(order)
        await db_session.flush()

        attribution = GmvAttribution(
            tenant_id=UUID(tid),
            external_order_id=order.id,
            public_id="GMV_CODE",
            amount=299.0,
            match_type="phone",
        )
        db_session.add(attribution)
        await db_session.commit()

        resp = await client.get("/api/v1/gmv/dashboard", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_gmv"] >= 299.0
        assert data["attributed_orders"] >= 1

    @pytest.mark.anyio
    async def test_gmv_by_public_id(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.get(
            "/api/v1/gmv/dashboard",
            params={"group_by": "public_id"},
            headers=headers,
        )
        assert resp.status_code == 200


class TestTransactionRecord:
    """W16-004: 外部成交事件记录"""

    @pytest.mark.anyio
    async def test_attribution_record(self, client: AsyncClient, setup_tenant, db_session: AsyncSession):
        tid, headers = setup_tenant

        order = ExternalOrder(
            tenant_id=UUID(tid),
            external_id="ATTR-ORD",
            amount=88.0,
            phone_hash=hash_phone("13800138002"),
            product_name="归因测试",
            order_time=datetime.now(UTC),
            matched=True,
        )
        db_session.add(order)
        await db_session.flush()

        attr = GmvAttribution(
            tenant_id=UUID(tid),
            external_order_id=order.id,
            public_id="ATTR_CODE",
            amount=88.0,
            match_type="manual",
        )
        db_session.add(attr)
        await db_session.commit()

        # 查询归因记录
        resp = await client.get("/api/v1/gmv/attributions", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["items"][0]["amount"] == 88.0
        assert data["items"][0]["match_type"] == "manual"
