"""A7-007: 统计看板 API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.analytics import DailyScanStats
from app.models.campaign import BenefitClaim, Campaign
from app.models.channel import DiversionClue
from app.models.export_log import ExportLog
from app.models.risk import RiskAlert
from app.models.scan import ScanEvent
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
async def dashboard_setup(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "看板测试",
            "admin_email": "dashboard@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 插入汇总数据
    today = date.today()
    for i in range(7):
        s = DailyScanStats(
            tenant_id=uuid.UUID(tid),
            date=today.replace(day=max(1, today.day - i)) if today.day > i else today,
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
    async def test_export_scan_events_with_date_range(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
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
            params={"export_type": "scan_events", "start_date": start, "end_date": end},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "SCAN000" in resp.text

        # 验证 export_log 写入
        result = await db_session.execute(
            select(ExportLog).where(ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "scan_events_csv")
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count == 3

    @pytest.mark.anyio
    async def test_export_campaign_dashboard(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        campaign = Campaign(
            tenant_id=tenant_uuid,
            name="测试活动",
            campaign_type="coupon",
            status="active",
            start_at="2024-01-01",
            end_at="2024-12-31",
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
            params={"export_type": "campaign_dashboard"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "测试活动" in resp.text

        result = await db_session.execute(
            select(ExportLog).where(ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "campaign_dashboard_csv")
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count >= 1

    @pytest.mark.anyio
    async def test_export_risk_dashboard(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        alert = RiskAlert(
            tenant_id=tenant_uuid,
            alert_type="multi_location",
            public_id="RISK001",
            code_item_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            detail="测试预警",
        )
        db_session.add(alert)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/analytics/exports",
            params={"export_type": "risk_dashboard"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "RISK001" in resp.text

        result = await db_session.execute(
            select(ExportLog).where(ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "risk_dashboard_csv")
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count >= 1

    @pytest.mark.anyio
    async def test_export_regional_dashboard(self, client: AsyncClient, db_session: AsyncSession, dashboard_setup):
        tid, headers = dashboard_setup
        tenant_uuid = uuid.UUID(tid)

        clue = DiversionClue(
            tenant_id=tenant_uuid,
            public_id="DIV001",
            code_item_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            expected_region="上海",
            detected_city="北京",
            resolved=False,
        )
        db_session.add(clue)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/analytics/exports",
            params={"export_type": "regional_dashboard"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "DIV001" in resp.text

        result = await db_session.execute(
            select(ExportLog).where(ExportLog.tenant_id == tenant_uuid, ExportLog.export_type == "regional_dashboard_csv")
        )
        log = result.scalar_one_or_none()
        assert log is not None
        assert log.row_count >= 1

    @pytest.mark.anyio
    async def test_export_permission_denied_for_non_admin(self, client: AsyncClient, dashboard_setup):
        tid, _ = dashboard_setup
        operator_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        operator_headers = {"Authorization": f"Bearer {operator_token}"}

        resp = await client.post(
            "/api/v1/analytics/exports",
            params={"export_type": "scan_events"},
            headers=operator_headers,
        )
        assert resp.status_code == 403
