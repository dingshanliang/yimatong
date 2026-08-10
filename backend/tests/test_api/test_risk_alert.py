"""W10: 轻量验真与异常预警测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import risk as risk_api
from app.core.database import get_db
from app.main import app
from app.models.code import CodeItem, CodeItemStatus
from app.models.risk import RiskAlert, RiskAlertType
from app.models.scan import ScanEvent
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
            "name": "预警测试租户",
            "admin_email": "risk@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    feature_resp = await client.patch(
        f"/api/v1/tenants/{tid}",
        json={"enabled_features": {"risk_module": True}},
        headers=_platform_admin_headers(),
    )
    assert feature_resp.status_code == 200
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
        headers={
            **headers,
            "Idempotency-Key": str(uuid5(NAMESPACE_URL, f"risk-alert:{batch_code}")),
        },
    )
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]
    exported = await client.post(f"/api/v1/code-batches/{batch_id}/export", headers=headers)
    printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    delivered = await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "risk lifecycle test handoff", "recipient": "risk test", "confirm": "deliver"},
        headers=headers,
    )
    activated = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert exported.status_code == printing.status_code == delivered.status_code == activated.status_code == 200
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

        alert = await check_multi_location(
            db_session,
            UUID(tid),
            "TESTCODE001",
            "ip_hash_2",
            code_item_id=UUID("00000000-0000-0000-0000-000000000091"),
        )
        assert alert is not None
        assert alert.alert_type == RiskAlertType.multi_location

    @pytest.mark.anyio
    async def test_multi_location_does_not_fabricate_code_reference_for_another_tenant(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        _, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "ALERT-TENANT-BOUNDARY-1")
        public_id = items[0]["public_id"]
        other_tenant_id = UUID("00000000-0000-0000-0000-000000000092")
        for i in range(2):
            db_session.add(
                ScanEvent(
                    tenant_id=other_tenant_id,
                    public_id=public_id,
                    scan_time=datetime.now(UTC),
                    ip_hash=f"other_tenant_ip_{i}",
                    is_first_scan=i == 0,
                )
            )
        await db_session.flush()

        from app.services.risk import check_multi_location

        alert = await check_multi_location(db_session, other_tenant_id, public_id, "other_tenant_ip_1")

        assert alert is None

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

        alert = await check_suspected_copy(
            db_session,
            UUID(tid),
            "TESTCODE003",
            "same_ip",
            code_item_id=UUID("00000000-0000-0000-0000-000000000093"),
        )
        assert alert is not None
        assert alert.alert_type == RiskAlertType.suspected_copy

    @pytest.mark.anyio
    async def test_suspected_copy_does_not_create_alert_without_authoritative_code(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, *_ = setup_tenant
        for _ in range(12):
            db_session.add(
                ScanEvent(
                    tenant_id=UUID(tid),
                    public_id="MISSINGCODE001",
                    scan_time=datetime.now(UTC),
                    ip_hash="same_ip",
                    is_first_scan=False,
                )
            )
        await db_session.flush()

        from app.services.risk import check_suspected_copy

        alert = await check_suspected_copy(db_session, UUID(tid), "MISSINGCODE001", "same_ip")

        assert alert is None


class TestFreezeCode:
    """W10-005: 风险冻结流程"""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "body",
        [
            None,
            {"reason": "   ", "confirm": "freeze"},
            {"reason": "x" * 201, "confirm": "freeze"},
            {"reason": "investigation", "confirm": "void"},
            {"reason": "investigation", "confirm": "freeze", "unexpected": True},
        ],
    )
    async def test_freeze_requires_strict_reason_and_confirmation(
        self,
        client: AsyncClient,
        setup_tenant,
        body,
    ):
        _, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, f"FREEZE-VALID-{len(str(body))}")

        resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{items[0]['id']}/freeze",
            json=body,
            headers=headers,
        )

        assert resp.status_code == 422

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("sqlstate", "expected_status", "expected_error_code"),
        [
            ("23503", 404, "NOT_FOUND"),
            ("22023", 409, "CODE_LIFECYCLE_CONFLICT"),
        ],
    )
    async def test_freeze_database_failures_have_stable_public_mapping(
        self,
        client: AsyncClient,
        setup_tenant,
        monkeypatch,
        sqlstate,
        expected_status,
        expected_error_code,
    ):
        class LifecycleFailure(Exception):
            pass

        failure = LifecycleFailure()
        failure.sqlstate = sqlstate
        _, headers, *_ = setup_tenant
        monkeypatch.setattr(
            risk_api,
            "freeze_code_item",
            AsyncMock(side_effect=DBAPIError("statement", {}, failure)),
        )

        response = await client.post(
            "/api/v1/risk-alerts/code-items/00000000-0000-0000-0000-000000000999/freeze",
            json={"reason": "investigation", "confirm": "freeze"},
            headers=headers,
        )

        assert response.status_code == expected_status
        assert response.json()["error_code"] == expected_error_code

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
            json={"reason": "manual risk investigation", "confirm": "freeze"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "frozen"
        audit = await client.get("/api/v1/audit-logs?action=code_freeze", headers=headers)
        assert audit.status_code == 200
        assert audit.json()["total"] == 1
        assert audit.json()["items"][0]["operator"]["id"] == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.anyio
    async def test_freeze_audit_failure_rolls_back_lifecycle_provenance_and_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
        monkeypatch,
    ):
        tid, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-ROLLBACK-1")
        item_id = UUID(items[0]["id"])

        async def rollbacking_get_db():
            transaction = await db_session.begin_nested()
            try:
                yield db_session
                await transaction.commit()
            except BaseException:
                await transaction.rollback()
                raise

        app.dependency_overrides[get_db] = rollbacking_get_db
        monkeypatch.setattr(
            "app.services.audit.write_audit_log",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
                json={"reason": "rollback investigation", "confirm": "freeze"},
                headers=headers,
            )

        item = await db_session.get(CodeItem, item_id)
        await db_session.refresh(item)
        assert item.status == CodeItemStatus.activated
        assert item.frozen_from_status is None
        assert item.frozen_at is None
        assert item.frozen_by is None
        assert item.freeze_reason is None
        assert item.freeze_provenance_version is None
        alert_count = await db_session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(RiskAlert.tenant_id == UUID(tid), RiskAlert.code_item_id == item_id)
        )
        assert alert_count == 0

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
        await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "traceability investigation", "confirm": "freeze"},
            headers=headers,
        )

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

        await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "recover flow", "confirm": "freeze"},
            headers=headers,
        )
        resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "activated"

    @pytest.mark.anyio
    async def test_unfreeze_restores_bound_status(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-BOUND-1")
        item_id = items[0]["id"]
        bound = await client.post(f"/api/v1/code-items/{item_id}/bind", headers=headers)
        assert bound.status_code == 200
        assert bound.json()["status"] == "bound"
        frozen = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "bound code investigation", "confirm": "freeze"},
            headers=headers,
        )
        assert frozen.status_code == 200

        recovered = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
            headers=headers,
        )

        assert recovered.status_code == 200
        assert recovered.json()["status"] == "bound"

    @pytest.mark.anyio
    async def test_voided_code_is_absorbing_for_freeze_and_recover(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers, product_id, sku_id = setup_tenant
        items = await _create_and_activate_batch(client, headers, product_id, sku_id, "FREEZE-VOIDED-1")
        item_id = items[0]["id"]
        voided = await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            json={"reason": "permanent safety incident", "confirm": "void"},
            headers=headers,
        )
        assert voided.status_code == 200

        frozen = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "must remain terminal", "confirm": "freeze"},
            headers=headers,
        )
        recovered = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
            headers=headers,
        )

        assert frozen.status_code == 409
        assert recovered.status_code == 409
        current = await client.get(f"/api/v1/code-items/{item_id}", headers=headers)
        assert current.status_code == 200
        assert current.json()["status"] == "revoked"


class TestRiskAlertAPI:
    """W10-006: 后台预警查询 API"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["operator", "viewer"])
    async def test_non_admin_cannot_list_risk_alerts(self, client: AsyncClient, setup_tenant, role: str):
        tid, _, *_ = setup_tenant
        token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", role)

        resp = await client.get(
            "/api/v1/risk-alerts",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Missing permission: code:manage"

    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["operator", "viewer"])
    async def test_non_admin_cannot_resolve_risk_alert(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
        role: str,
    ):
        tid, _, *_ = setup_tenant
        alert = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.multi_location,
            public_id="VIEWERDENIED01",
            code_item_id=UUID("00000000-0000-0000-0000-000000000099"),
            detail="viewer must not resolve",
        )
        db_session.add(alert)
        await db_session.commit()
        token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", role)

        resp = await client.post(
            f"/api/v1/risk-alerts/{alert.id}/resolve",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Missing permission: code:manage"

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
        audit = await client.get("/api/v1/audit-logs?action=risk_alert_resolved", headers=headers)
        assert audit.status_code == 200
        assert audit.json()["total"] == 1
        event = audit.json()["items"][0]
        assert event["operator"]["id"] == "00000000-0000-0000-0000-000000000001"
        assert event["details"] == {
            "public_id": items[0]["public_id"],
            "before": {"resolved": False},
            "after": {"resolved": True},
        }

    @pytest.mark.anyio
    async def test_resolve_audit_failure_rolls_back_alert_state(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
        monkeypatch,
    ):
        tid, headers, *_ = setup_tenant
        alert = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.suspected_copy,
            public_id="ROLLBACKALERT01",
            code_item_id=UUID("00000000-0000-0000-0000-000000000094"),
            detail="audit rollback",
        )
        db_session.add(alert)
        await db_session.commit()

        async def rollbacking_get_db():
            transaction = await db_session.begin_nested()
            try:
                yield db_session
                await transaction.commit()
            except BaseException:
                await transaction.rollback()
                raise

        app.dependency_overrides[get_db] = rollbacking_get_db
        monkeypatch.setattr(
            "app.services.audit.write_audit_log",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(f"/api/v1/risk-alerts/{alert.id}/resolve", headers=headers)

        await db_session.refresh(alert)
        assert alert.resolved is False

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
