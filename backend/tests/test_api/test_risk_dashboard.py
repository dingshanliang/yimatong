"""W14: 渠道风控看板测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.channel import Distributor, DiversionClue, Region
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
            "name": "看板测试租户",
            "admin_email": "dashboard@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestRepeatScanStats:
    """W14-001: 重复扫码统计"""

    @pytest.mark.anyio
    async def test_repeat_scan_stats(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        # 创建一些扫码事件
        for i in range(5):
            event = ScanEvent(
                tenant_id=UUID(tid),
                public_id="CODE001",
                scan_time=datetime.now(UTC),
                ip_hash=f"ip_{i % 2}",
            )
            db_session.add(event)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/repeat-scans",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        assert data["items"][0]["public_id"] == "CODE001"
        assert data["items"][0]["scan_count"] == 5

    @pytest.mark.anyio
    async def test_repeat_scan_with_threshold(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        # 少量扫码
        event = ScanEvent(
            tenant_id=UUID(tid),
            public_id="CODE002",
            scan_time=datetime.now(UTC),
            ip_hash="ip_1",
        )
        db_session.add(event)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/repeat-scans",
            params={"min_count": 3},
            headers=headers,
        )
        assert resp.status_code == 200
        # CODE002 只有 1 次扫描，不应出现
        ids = [item["public_id"] for item in resp.json()["items"]]
        assert "CODE002" not in ids


class TestCrossRegionStats:
    """W14-002: 跨区扫码检测统计"""

    @pytest.mark.anyio
    async def test_cross_region_stats(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        # 创建经销商和区域
        dist = Distributor(tenant_id=UUID(tid), name="华东经销商", code="D001")
        db_session.add(dist)
        await db_session.flush()

        region = Region(
            tenant_id=UUID(tid),
            name="上海",
            code="R001",
            province="上海",
            city="上海",
            distributor_id=dist.id,
        )
        db_session.add(region)
        await db_session.flush()

        # 创建窜货线索
        clue = DiversionClue(
            tenant_id=UUID(tid),
            public_id="CODE003",
            code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_region="上海",
            detected_city="北京",
            distributor_id=dist.id,
            ip_hash="ip_cross",
            resolved=False,
        )
        db_session.add(clue)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/cross-region",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_clues"] >= 1
        assert len(data["by_region"]) >= 1


class TestDiversionSummary:
    """W14-003: 疑似窜货线索汇总"""

    @pytest.mark.anyio
    async def test_diversion_summary(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        dist = Distributor(tenant_id=UUID(tid), name="华南经销商", code="D002")
        db_session.add(dist)
        await db_session.flush()

        for i in range(3):
            clue = DiversionClue(
                tenant_id=UUID(tid),
                public_id=f"DIV{i}",
                code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
                expected_region="广州",
                detected_city="成都",
                distributor_id=dist.id,
                ip_hash=f"ip_div_{i}",
                resolved=False,
            )
            db_session.add(clue)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/diversion-summary",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["by_distributor"]) >= 1

    @pytest.mark.anyio
    async def test_diversion_unresolved_count(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        # 1 个已解决，1 个未解决
        for resolved in [True, False]:
            clue = DiversionClue(
                tenant_id=UUID(tid),
                public_id=f"RES{'Y' if resolved else 'N'}",
                code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
                expected_region="深圳",
                detected_city="武汉",
                distributor_id=None,
                ip_hash="ip_res",
                resolved=resolved,
            )
            db_session.add(clue)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/diversion-summary",
            params={"resolved": False},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.anyio
    async def test_resolve_diversion_records_note_and_operator(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        clue = DiversionClue(
            tenant_id=UUID(tid),
            public_id="NOTE001",
            code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_region="上海",
            detected_city="北京",
            ip_hash="ip_note",
            resolved=False,
        )
        db_session.add(clue)
        await db_session.commit()

        resp = await client.put(
            f"/api/v1/risk-dashboard/diversion-clues/{clue.id}/resolve",
            json={"resolution_action": "confirmed_diversion", "resolution_note": "已联系经销商核实为临时调货"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolved"] is True
        assert data["resolution_action"] == "confirmed_diversion"
        assert data["resolution_note"] == "已联系经销商核实为临时调货"
        assert data["resolved_by_account_id"] == "00000000-0000-0000-0000-000000000001"
        assert data["resolved_at"] is not None


class TestRiskExport:
    """W14-004: 风控数据导出"""

    @pytest.mark.anyio
    async def test_export_risk_alerts_csv(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        alert = RiskAlert(
            tenant_id=UUID(tid),
            alert_type=RiskAlertType.multi_location,
            public_id="EXP001",
            code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
            detail="测试导出",
        )
        db_session.add(alert)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/export",
            params={"data_type": "alerts"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "EXP001" in resp.text

    @pytest.mark.anyio
    async def test_export_diversion_csv(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant

        clue = DiversionClue(
            tenant_id=UUID(tid),
            public_id="EXP002",
            code_item_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_region="上海",
            detected_city="北京",
            ip_hash="ip_exp",
            resolved=False,
        )
        db_session.add(clue)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/export",
            params={"data_type": "diversions"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "EXP002" in resp.text


class TestSSEAlertStream:
    """SSE 告警流 — query parameter 认证"""

    @pytest.mark.anyio
    async def test_sse_with_valid_token(self, client: AsyncClient, setup_tenant):
        from unittest.mock import patch

        tid, headers = setup_tenant
        ticket_response = await client.post(
            "/api/v1/risk-dashboard/alerts/ticket",
            headers=headers,
        )
        assert ticket_response.status_code == 200
        ticket = ticket_response.json()["ticket"]

        # Patch StreamingResponse 为同步返回，避免无限流卡住测试
        from fastapi.responses import StreamingResponse as OrigStreamingResponse  # noqa: N814

        captured: list = []

        class FiniteSR(OrigStreamingResponse):
            def __init__(self, content, *args, **kwargs):
                # 替换 generator 为有限版本
                async def _finite():
                    yield ": connected\n\n"

                super().__init__(_finite(), *args, **kwargs)
                captured.append(True)

        with patch("app.api.v1.risk_dashboard.StreamingResponse", FiniteSR):
            resp = await client.get(
                "/api/v1/risk-dashboard/alerts/stream",
                params={"ticket": ticket},
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_sse_without_token_returns_422(self, client: AsyncClient, setup_tenant):
        resp = await client.get("/api/v1/risk-dashboard/alerts/stream")
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_sse_with_invalid_token_returns_401(self, client: AsyncClient, setup_tenant):
        resp = await client.get(
            "/api/v1/risk-dashboard/alerts/stream",
            params={"ticket": "invalid-token"},
        )
        assert resp.status_code == 401
