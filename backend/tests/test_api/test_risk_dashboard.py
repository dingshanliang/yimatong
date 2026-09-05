"""W14: 渠道风控看板测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import risk_dashboard as risk_dashboard_api
from app.core.database import get_db
from app.main import app
from app.models.channel import Distributor, DiversionClue, Region
from app.models.risk import RiskAlert, RiskAlertType
from app.models.scan import ScanEvent
from app.schemas.diversion import DiversionEvidenceCreate, DiversionTransitionRequest
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def test_diversion_mutation_schemas_are_strict_bounded_and_state_aware():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DiversionTransitionRequest(
            expected_version=1,
            to_status="confirmed_diversion",
            reason="done",
            resolution_note=None,
        )
    with pytest.raises(ValidationError):
        DiversionTransitionRequest(expected_version=1, to_status="open", reason="reopen", resolution_note="overwrite")
    with pytest.raises(ValidationError):
        DiversionEvidenceCreate(expected_version=1, evidence_type="explanation")
    with pytest.raises(ValidationError):
        DiversionEvidenceCreate(
            expected_version=1,
            evidence_type="logistics",
            file_url="https://evidence.example/file.pdf",
        )
    evidence = DiversionEvidenceCreate(
        expected_version=1,
        evidence_type="explanation",
        description="渠道书面说明",
    )
    assert evidence.description == "渠道书面说明"


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
    feature_resp = await client.patch(
        f"/api/v1/tenants/{tid}",
        json={"enabled_features": {"risk_module": True}},
        headers=_platform_admin_headers(),
    )
    assert feature_resp.status_code == 200
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
        # 风控中心「未处理窜货线索」指标直接消费该字段
        assert data["unresolved_count"] == 3
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
                investigation_status="false_positive" if resolved else "open",
                resolution_action="false_positive" if resolved else None,
                resolution_note="verified false positive" if resolved else None,
                resolved_by_account_id=(UUID("00000000-0000-0000-0000-000000000001") if resolved else None),
                resolved_at=datetime.now(UTC) if resolved else None,
            )
            db_session.add(clue)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/risk-dashboard/diversion-summary",
            params={"resolved": False},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        # resolved=false 过滤时 total 即未处理数；unresolved_count 与过滤解耦，仍统计全租户未处理
        assert body["unresolved_count"] == 1

    @pytest.mark.anyio
    async def test_transition_diversion_uses_strict_authority_contract(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
        monkeypatch,
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

        from app.api.v1 import risk_dashboard as risk_api

        authority = AsyncMock(
            return_value={
                "resource_id": clue.id,
                "clue_id": clue.id,
                "clue_version": 2,
                "investigation_status": "confirmed_diversion",
                "resolved": True,
                "observation_count": 1,
                "replayed": False,
                "actor_id": uuid4(),
                "recorded_at": datetime.now(UTC),
            }
        )
        monkeypatch.setattr(risk_api.diversion_authority, "transition_clue", authority)
        resp = await client.post(
            f"/api/v1/risk-dashboard/diversion-clues/{clue.id}/transition",
            json={
                "expected_version": 1,
                "to_status": "confirmed_diversion",
                "reason": "complete investigation",
                "resolution_note": "已联系经销商核实为临时调货",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert resp.status_code == 200
        authority.assert_awaited_once()

    @pytest.mark.anyio
    async def test_transition_rejects_invalid_contract_before_authority(
        self, client: AsyncClient, setup_tenant, monkeypatch
    ):
        _tid, headers = setup_tenant
        from app.api.v1 import risk_dashboard as risk_api

        authority = AsyncMock()
        monkeypatch.setattr(risk_api.diversion_authority, "transition_clue", authority)
        clue_id = uuid4()
        response = await client.post(
            f"/api/v1/risk-dashboard/diversion-clues/{clue_id}/transition",
            json={"expected_version": 1, "to_status": "confirmed_diversion", "reason": "done"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 422
        authority.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["viewer", "distributor", "store_guide"])
    async def test_unauthorized_roles_cannot_transition_before_authority(
        self, client: AsyncClient, setup_tenant, monkeypatch, role: str
    ):
        tid, _headers = setup_tenant
        from app.api.v1 import risk_dashboard as risk_api

        authority = AsyncMock()
        monkeypatch.setattr(risk_api.diversion_authority, "transition_clue", authority)
        token = create_access_token(tid, str(uuid4()), role)
        response = await client.post(
            f"/api/v1/risk-dashboard/diversion-clues/{uuid4()}/transition",
            json={
                "expected_version": 1,
                "to_status": "false_positive",
                "reason": "review complete",
                "resolution_note": "not a diversion",
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 403
        authority.assert_not_awaited()


class TestRiskExport:
    """W14-004: 风控数据导出"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("reason", [None, "   ", "x" * 501])
    async def test_reason_validation_precedes_risk_query(self, client: AsyncClient, setup_tenant, monkeypatch, reason):
        _, headers = setup_tenant
        export = AsyncMock()
        monkeypatch.setattr(risk_dashboard_api, "export_risk_data", export)
        body = {"data_type": "alerts"}
        if reason is not None:
            body["reason"] = reason

        response = await client.post(
            "/api/v1/risk-dashboard/export",
            json=body,
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )

        assert response.status_code == 422
        export.assert_not_awaited()

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

        resp = await client.post(
            "/api/v1/risk-dashboard/export",
            json={"data_type": "alerts", "reason": "风控月度复盘"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
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

        resp = await client.post(
            "/api/v1/risk-dashboard/export",
            json={"data_type": "diversions", "reason": "窜货线索复核"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
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
