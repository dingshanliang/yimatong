"""A7-007: 统计看板 API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import analytics_dashboard as analytics_dashboard_api
from app.core.database import get_db
from app.main import app
from app.models.analytics import DailyScanStats
from app.models.campaign import BenefitClaim, Campaign
from app.models.export_log import ExportLog
from app.models.regional import RegionalOrg
from app.models.scan import ScanEvent
from app.models.tenant import Tenant, TenantStatus, TenantType
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _export_headers(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(uuid.uuid4())}


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
async def dashboard_setup(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "看板测试",
            "admin_email": "dashboard@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 插入汇总数据
    today = date.today()
    for i in range(7):
        s = DailyScanStats(
            tenant_id=uuid.UUID(tid),
            date=today - timedelta(days=i),
            total_scans=100 + i,
            uv=50 + i,
            first_scans=20 + i,
            rescans=80 + i,
        )
        db_session.add(s)
    await db_session.commit()

    return tid, headers


class TestDashboardAPI:
    @pytest.mark.anyio
    async def test_dashboard_returns_aggregated_data(self, client: AsyncClient, dashboard_setup):
        _, headers = dashboard_setup
        resp = await client.get(
            "/api/v1/analytics/dashboard",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "today_scans" in data
        assert "cumulative_scans" in data
        assert "cumulative_first_scans" in data
        assert "trend" in data
        assert "environment_breakdown" in data
        assert data["cumulative_scans"] > 0

    @pytest.mark.anyio
    async def test_dashboard_empty_data(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "空看板",
                "admin_email": "empty@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tid = resp.json()["id"]
        token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/api/v1/analytics/dashboard",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["today_scans"] == 0
        assert data["cumulative_scans"] == 0


class TestDashboardExport:
    """yimatong-0j6: 扫码看板数据导出"""

    @pytest.mark.anyio
    async def test_export_log_list_returns_only_safe_projection(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        dashboard_setup,
    ):
        tenant_id, headers = dashboard_setup
        db_session.add(
            ExportLog(
                tenant_id=uuid.UUID(tenant_id),
                account_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                export_type="scan_events_xlsx",
                file_name="safe.xlsx",
                row_count=1,
            )
        )
        await db_session.flush()

        response = await client.get("/api/v1/analytics/exports", headers=headers)

        assert response.status_code == 200
        assert set(response.json()["items"][0]) == {
            "id",
            "export_type",
            "resource_id",
            "file_name",
            "row_count",
            "status",
            "created_at",
        }

    @pytest.mark.anyio
    async def test_export_log_list_rejects_viewer(self, client: AsyncClient, dashboard_setup):
        tenant_id, _ = dashboard_setup
        viewer = create_access_token(
            tenant_id,
            "00000000-0000-0000-0000-000000000003",
            "viewer",
        )

        response = await client.get(
            "/api/v1/analytics/exports",
            headers={"Authorization": f"Bearer {viewer}"},
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_export_log_list_rejects_base_agency_admin(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        agency = Tenant(
            name="导出审计代运营隔离",
            slug=f"agency-export-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.agency,
        )
        db_session.add(agency)
        await db_session.flush()
        agency_admin = create_access_token(
            str(agency.id),
            "00000000-0000-0000-0000-000000000004",
            "admin",
            "agency",
        )

        response = await client.get(
            "/api/v1/analytics/exports",
            headers={"Authorization": f"Bearer {agency_admin}"},
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_export_scan_events_with_date_range(
        self, client: AsyncClient, db_session: AsyncSession, dashboard_setup
    ):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        # 插入扫码事件
        for i in range(3):
            event = ScanEvent(
                tenant_id=tenant_uuid,
                public_id=f"SCAN{i:03d}",
                scan_time=datetime.now(UTC) - timedelta(days=i),
                is_first_scan=(i == 0),
                environment="wechat",
            )
            db_session.add(event)
        await db_session.commit()

        start = (date.today() - timedelta(days=5)).isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()

        resp = await client.post(
            "/api/v1/analytics/exports",
            json={
                "export_type": "scan_events",
                "start_date": start,
                "end_date": end,
                "reason": "  月度扫码复盘  ",
            },
            headers=_export_headers(headers),
        )
        assert resp.status_code == 200
        assert "spreadsheetml.sheet" in resp.headers.get("content-type", "")

        # 验证 export_log 写入
        result = await db_session.execute(
            select(ExportLog).where(ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "scan_events_xlsx")
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count == 3
        assert log.reason == "月度扫码复盘"
        assert log.scope_snapshot["start_date"] == start
        assert resp.headers["X-Content-SHA256"] == log.checksum_sha256
        assert int(resp.headers["Content-Length"]) == log.artifact_size_bytes

    @pytest.mark.anyio
    async def test_export_xlsx_replays_same_bytes_for_same_idempotency_key(
        self, client: AsyncClient, db_session: AsyncSession, dashboard_setup
    ):
        tenant_id, headers = dashboard_setup
        request_headers = {**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"}
        body = {"export_type": "scan_stats", "reason": "固定报表重试"}

        first = await client.post("/api/v1/analytics/exports", json=body, headers=request_headers)
        second = await client.post("/api/v1/analytics/exports", json=body, headers=request_headers)

        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert first.headers["X-Export-Id"] == second.headers["X-Export-Id"]
        assert first.headers["X-Content-SHA256"] == second.headers["X-Content-SHA256"]
        count = await db_session.scalar(
            select(func.count())
            .select_from(ExportLog)
            .where(
                ExportLog.tenant_id == uuid.UUID(tenant_id),
                ExportLog.idempotency_key == request_headers["Idempotency-Key"],
            )
        )
        assert count == 1

        conflict = await client.post(
            "/api/v1/analytics/exports",
            json={"export_type": "scan_stats", "reason": "不同用途"},
            headers=request_headers,
        )
        assert conflict.status_code == 409

    @pytest.mark.anyio
    async def test_export_campaign_dashboard(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        campaign = Campaign(
            tenant_id=tenant_uuid,
            name="测试活动",
            campaign_type="coupon",
            status="active",
            start_at=datetime(2024, 1, 1, tzinfo=UTC),
            end_at=datetime(2024, 12, 31, tzinfo=UTC),
        )
        db_session.add(campaign)
        await db_session.commit()
        await db_session.refresh(campaign)

        claim = BenefitClaim(
            tenant_id=tenant_uuid,
            campaign_id=campaign.id,
            consumer_id="consumer-001",
            benefit_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            idempotency_key="key-001",
            status="claimed",
        )
        db_session.add(claim)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/analytics/exports",
            json={
                "export_type": "campaign_dashboard",
                "campaign_id": str(campaign.id),
                "reason": "活动效果复盘",
            },
            headers=_export_headers(headers),
        )
        assert resp.status_code == 200
        assert "spreadsheetml.sheet" in resp.headers.get("content-type", "")

        result = await db_session.execute(
            select(ExportLog).where(
                ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "campaign_dashboard_xlsx"
            )
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count >= 1

    @pytest.mark.anyio
    async def test_risk_export_compatibility_contract(
        self, client: AsyncClient, db_session: AsyncSession, dashboard_setup
    ):
        tenant_id, headers = dashboard_setup
        resp = await client.post(
            "/api/v1/analytics/exports",
            json={"export_type": "risk_dashboard", "reason": "月度风控汇总"},
            headers=_export_headers(headers),
        )
        assert resp.status_code == 200
        log = await db_session.scalar(
            select(ExportLog).where(
                ExportLog.tenant_id == uuid.UUID(tenant_id),
                ExportLog.export_type == "risk_dashboard_xlsx",
            )
        )
        assert log is not None
        assert log.reason == "月度风控汇总"
        assert log.scope_snapshot["read_model"] == "/api/v1/analytics/risk-dashboard"
        assert resp.headers["X-Content-SHA256"] == log.checksum_sha256

    @pytest.mark.anyio
    async def test_export_regional_dashboard(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        org = RegionalOrg(tenant_id=tenant_uuid, name="区域协会", org_type="association")
        db_session.add(org)
        await db_session.commit()
        await db_session.refresh(org)

        resp = await client.post(
            "/api/v1/analytics/exports",
            json={
                "export_type": "regional_dashboard",
                "org_id": str(org.id),
                "days_back": 30,
                "reason": "区域成员经营复盘",
            },
            headers=_export_headers(headers),
        )
        assert resp.status_code == 200
        assert "spreadsheetml.sheet" in resp.headers.get("content-type", "")

        result = await db_session.execute(
            select(ExportLog).where(
                ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "regional_dashboard_xlsx"
            )
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count == 5
        assert log.scope_snapshot == {
            "days_back": 30,
            "dimensions": [
                "member_count",
                "product_count",
                "total_scans",
                "total_claims",
                "days_back",
                "by_member",
                "by_product",
            ],
            "org_id": str(org.id),
        }

    @pytest.mark.anyio
    async def test_export_permission_denied_for_non_admin(self, client: AsyncClient, dashboard_setup):
        tid, _ = dashboard_setup
        operator_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        operator_headers = {"Authorization": f"Bearer {operator_token}"}

        resp = await client.post(
            "/api/v1/analytics/exports",
            json={"export_type": "scan_events", "reason": "越权测试"},
            headers=_export_headers(operator_headers),
        )
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_export_rejects_acting_agency_session(self, client: AsyncClient, dashboard_setup):
        tenant_id, _ = dashboard_setup
        token = create_access_token(
            tenant_id,
            "00000000-0000-0000-0000-000000000001",
            "admin",
            "agency",
            extra={"acting_tenant_id": tenant_id, "scope": ["analytics:view"]},
        )

        response = await client.post(
            "/api/v1/analytics/exports",
            json={"export_type": "scan_events", "reason": "代理越权测试"},
            headers=_export_headers({"Authorization": f"Bearer {token}"}),
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    @pytest.mark.parametrize("reason", [None, "   ", "x" * 501])
    async def test_export_reason_is_required_before_ledger_write(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        dashboard_setup,
        monkeypatch,
        reason,
    ):
        tenant_id, headers = dashboard_setup
        rate_limit = AsyncMock()
        monkeypatch.setattr(analytics_dashboard_api, "enforce_export_rate_limit", rate_limit)
        before = await db_session.scalar(
            select(func.count()).select_from(ExportLog).where(ExportLog.tenant_id == uuid.UUID(tenant_id))
        )
        body = {"export_type": "scan_events"}
        if reason is not None:
            body["reason"] = reason

        response = await client.post(
            "/api/v1/analytics/exports",
            json=body,
            headers=_export_headers(headers),
        )

        assert response.status_code == 422
        after = await db_session.scalar(
            select(func.count()).select_from(ExportLog).where(ExportLog.tenant_id == uuid.UUID(tenant_id))
        )
        assert after == before
        rate_limit.assert_not_awaited()
