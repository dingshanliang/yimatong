"""W10: 轻量验真与异常预警测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.risk import RiskAlert, RiskAlertType
from app.models.scan import ScanEvent
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
            "name": "预警测试租户",
            "admin_email": "risk@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "测试品牌"}, headers=headers)
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "测试产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product.json()["id"], "code": "SKU-R", "name": "默认规格", "specifications": {}},
        headers=headers,
    )
    return tid, headers, product.json()["id"], sku.json()["id"]


async def _create_and_activate_batch(client, headers, product_id, sku_id, batch_code, quantity=5):
    """创建并激活码批次"""
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": batch_code,
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
        },
        headers=headers,
    )
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch.json()["id"],
            "quantity": quantity,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]
    await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    items_resp = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}",
        headers=headers,
    )
    return items_resp.json()["items"]


class TestMultiLocationAlert:
    """W10-003: 多地扫码预警检测"""

    @pytest.mark.anyio
    async def test_multi_location_creates_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        from app.services.risk import check_multi_location

        # 插入来自不同 IP 的扫码事件
        for i in range(3):
            event = ScanEvent(
                tenant_id=UUID(tid),
                public_id="TESTCODE001",
                scan_time=datetime.now(UTC),
                ip_hash=f"ip_hash_{i}",
                is_first_scan=(i == 0),
            )
            db_session.add(event)
        await db_session.flush()

        alert = await check_multi_location(db_session, UUID(tid), "TESTCODE001", "ip_hash_2")
        assert alert is not None
        assert alert.alert_type == RiskAlertType.multi_location

    @pytest.mark.anyio
    async def test_single_location_no_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        from app.services.risk import check_multi_location

        # 单一 IP 扫描不触发
        event = ScanEvent(
            tenant_id=UUID(tid),
            public_id="TESTCODE002",
            scan_time=datetime.now(UTC),
            ip_hash="same_ip",
            is_first_scan=True,
        )
        db_session.add(event)
        await db_session.flush()

        alert = await check_multi_location(db_session, UUID(tid), "TESTCODE002", "same_ip")
        assert alert is None


class TestSuspectedCopyAlert:
    """W10-004: 疑似复制码预警"""

    @pytest.mark.anyio
    async def test_high_frequency_creates_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        from app.services.risk import check_suspected_copy

        # 插入高频扫码事件
        for _ in range(12):
            event = ScanEvent(
                tenant_id=UUID(tid),
                public_id="TESTCODE003",
                scan_time=datetime.now(UTC),
                ip_hash="same_ip",
                is_first_scan=False,
            )
            db_session.add(event)
        await db_session.flush()

        alert = await check_suspected_copy(db_session, UUID(tid), "TESTCODE003", "same_ip")
        assert alert is not None
        assert alert.alert_type == RiskAlertType.suspected_copy


class TestFreezeCode:
    """W10-005: 风险冻结流程"""

    @pytest.mark.anyio
    async def test_freeze_activated_code(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-001")
        item_id = items[0]["id"]

        resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "frozen"

    @pytest.mark.anyio
    async def test_frozen_code_keeps_traceability(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        """yimatong-zgb1.6 AC3：冻结码保留溯源（200），不返回 403。

        旧契约：frozen → 403 错误页。
        新契约：frozen → 200 + lifecycle=frozen + 溯源资料 + 权益暂停。
        """
        tid, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-002")
        item_id = items[0]["id"]
        public_id = items[0]["public_id"]

        # 冻结
        await client.post(f"/api/v1/risk-alerts/code-items/{item_id}/freeze", headers=headers)

        # 访问 frozen 码（JSON 模式验证契约）
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # yimatong-zgb1.6：frozen 保留溯源，返回 200（不是 403）
        assert resp.status_code == 200
        body = resp.json()
        assert body["code_data"]["lifecycle"] == "frozen"
        # AC3：不颁发 scan_token（权益暂停）
        assert not body.get("scan_token")

    @pytest.mark.anyio
    async def test_unfreeze_code(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-003")
        item_id = items[0]["id"]

        await client.post(f"/api/v1/risk-alerts/code-items/{item_id}/freeze", headers=headers)
        resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "activated"


class TestRiskAlertAPI:
    """W10-006: 后台预警查询 API"""

    @pytest.mark.anyio
    async def test_list_alerts(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        # 创建预警记录
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "ALERT-001")
        alert = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.multi_location,
            public_id=items[0]["public_id"],
            code_item_id=UUID(items[0]["id"]),
            detail="测试预警",
        )
        db_session.add(alert)
        await db_session.commit()

        resp = await client.get("/api/v1/risk-alerts", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(a["alert_type"] == "multi_location" for a in data["items"])

    @pytest.mark.anyio
    async def test_resolve_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "ALERT-002")
        alert = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.suspected_copy,
            public_id=items[0]["public_id"],
            code_item_id=UUID(items[0]["id"]),
            detail="测试复制码",
        )
        db_session.add(alert)
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/risk-alerts/{alert.id}/resolve",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True

    @pytest.mark.anyio
    async def test_filter_by_type(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "ALERT-003")
        alert1 = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.multi_location,
            public_id=items[0]["public_id"],
            code_item_id=UUID(items[0]["id"]),
            detail="多地",
        )
        alert2 = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.suspected_copy,
            public_id=items[0]["public_id"],
            code_item_id=UUID(items[0]["id"]),
            detail="复制码",
        )
        db_session.add_all([alert1, alert2])
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-alerts?alert_type=multi_location",
            headers=headers,
        )
        data = resp.json()
        assert all(a["alert_type"] == "multi_location" for a in data["items"])
