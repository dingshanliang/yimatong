"""统计 API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.models.analytics import DailyScanStats
from app.models.campaign import BenefitClaim
from app.models.gmv import ExternalOrder, GmvAttribution, GmvAttributionConfirmation
from app.models.intent_event import IntentEvent
from app.services.analytics_extended import get_conversion_funnel
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

TENANT_ID = "019e887f-2d5d-70e2-a8ff-874a1d24791e"
ACCOUNT_ID = "019e887f-2d5d-70e2-a8ff-874a1d24791f"


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict:
    token = create_access_token(TENANT_ID, ACCOUNT_ID, "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_conversion_funnel_does_not_mask_source_failure_as_zero():
    db = AsyncMock()
    db.execute.side_effect = [Mock(scalar=Mock(return_value=0)), RuntimeError("intent source unavailable")]

    with pytest.raises(RuntimeError, match="intent source unavailable"):
        await get_conversion_funnel(db, uuid.UUID(TENANT_ID))


def _role_headers(role: str) -> dict:
    token = create_access_token(TENANT_ID, ACCOUNT_ID, role)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_viewer_is_denied_before_analytics_database_dependency(client: AsyncClient):
    called = False

    async def forbidden_db():
        nonlocal called
        called = True
        raise AssertionError("analytics DB dependency must not run before RBAC denial")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = forbidden_db

    response = await client.get(
        "/api/v1/analytics/conversion-funnel",
        headers=_role_headers("viewer"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: analytics:view"
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "operator"])
async def test_live_analytics_roles_can_read_conversion_funnel(client: AsyncClient, role: str):
    response = await client.get(
        "/api/v1/analytics/conversion-funnel",
        headers=_role_headers(role),
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/analytics/dashboard",
        "/api/v1/analytics/business-dashboard",
        "/api/v1/analytics/campaign-dashboard",
        "/api/v1/analytics/risk-dashboard",
        "/api/v1/analytics/regional-dashboard",
    ],
)
async def test_viewer_cannot_read_any_tenant_analytics_entry(client: AsyncClient, path: str):
    response = await client.get(path, headers=_role_headers("viewer"))

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: analytics:view"


@pytest.mark.asyncio
async def test_platform_cookie_and_api_key_cannot_enter_tenant_analytics(client: AsyncClient):
    platform = create_access_token("platform", "platform-admin", "platform_admin")

    platform_response = await client.get(
        "/api/v1/analytics/conversion-funnel",
        headers={"Cookie": f"platform_access_token={platform}"},
    )
    api_key_response = await client.get(
        "/api/v1/analytics/conversion-funnel",
        headers={"X-Api-Key": "not-a-tenant-jwt"},
    )

    assert platform_response.status_code == 401
    assert api_key_response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_viewer_takes_precedence_over_admin_cookie(client: AsyncClient):
    admin = create_access_token(TENANT_ID, ACCOUNT_ID, "admin")

    response = await client.get(
        "/api/v1/analytics/conversion-funnel",
        headers={
            **_role_headers("viewer"),
            "Cookie": f"access_token={admin}",
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_excludes_control_tenant_rows(client: AsyncClient, db_session: AsyncSession):
    tenant_id = uuid.UUID(TENANT_ID)
    control_tenant_id = uuid.uuid4()
    today = date.today()
    await db_session.execute(delete(DailyScanStats))
    db_session.add_all(
        [
            DailyScanStats(
                tenant_id=tenant_id,
                date=today,
                total_scans=3,
                uv=3,
                first_scans=2,
                rescans=1,
            ),
            DailyScanStats(
                tenant_id=control_tenant_id,
                date=today,
                total_scans=900,
                uv=900,
                first_scans=900,
                rescans=0,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/dashboard", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["cumulative_scans"] == 3


@pytest.mark.asyncio
async def test_dashboard_weekly_comparison_uses_two_disjoint_seven_day_windows(
    client: AsyncClient,
    db_session: AsyncSession,
):
    tenant_id = uuid.UUID(TENANT_ID)
    today = datetime.now(UTC).date()
    await db_session.execute(delete(DailyScanStats))
    for offset in range(14):
        current_week = offset < 7
        db_session.add(
            DailyScanStats(
                tenant_id=tenant_id,
                date=today - timedelta(days=offset),
                total_scans=10 if current_week else 5,
                uv=1,
                first_scans=4 if current_week else 2,
                rescans=6 if current_week else 3,
            )
        )
    db_session.add_all(
        [
            BenefitClaim(
                tenant_id=tenant_id,
                benefit_id=uuid.uuid4(),
                consumer_id="current-consumer",
                idempotency_key="current-claim",
                status="success",
                created_at=datetime.now(UTC) - timedelta(days=1),
            ),
            BenefitClaim(
                tenant_id=tenant_id,
                benefit_id=uuid.uuid4(),
                consumer_id="historical-consumer",
                idempotency_key="historical-claim",
                status="success",
                created_at=datetime.now(UTC) - timedelta(days=90),
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/dashboard", headers=_auth_headers())

    assert response.status_code == 200
    comparison = response.json()["comparison"]
    assert comparison["weekly_scans_change"] == {"value": 100.0, "direction": "up"}
    assert comparison["weekly_first_scans_change"] == {"value": 100.0, "direction": "up"}
    assert response.json()["period_claim_count"] == 1
    assert response.json()["period_claim_rate"] is None


@pytest.mark.asyncio
async def test_occurrence_and_cohort_metrics_use_trusted_clocks_and_confirmed_authority(
    client: AsyncClient,
    db_session: AsyncSession,
):
    tenant_id = uuid.UUID(TENANT_ID)
    now = datetime.now(UTC)
    confirmed_order = ExternalOrder(
        tenant_id=tenant_id,
        external_id="confirmed-order",
        amount=100,
        order_time=now - timedelta(days=1),
        source_system="trusted",
        ledger_original_amount=Decimal("100"),
        ledger_refunded_amount=Decimal("0"),
        ledger_cancelled_amount=Decimal("0"),
        ledger_net_amount=Decimal("100"),
        ledger_status="paid",
        matched=True,
    )
    unattributed_order = ExternalOrder(
        tenant_id=tenant_id,
        external_id="unattributed-order",
        amount=50,
        order_time=now - timedelta(days=1),
        source_system="trusted",
        ledger_original_amount=Decimal("50"),
        ledger_refunded_amount=Decimal("0"),
        ledger_cancelled_amount=Decimal("0"),
        ledger_net_amount=Decimal("50"),
        ledger_status="paid",
    )
    db_session.add_all([confirmed_order, unattributed_order])
    await db_session.flush()
    confirmed_attribution = GmvAttribution(
        tenant_id=tenant_id,
        external_order_id=confirmed_order.id,
        consumer_id=uuid.uuid4(),
        amount=100,
        original_amount=100,
        match_type="verified_consumer_scan",
        scan_time=now - timedelta(days=2),
        attribution_window_hours=720,
        confidence_score=1,
        authority_status="confirmed",
    )
    quarantined_attribution = GmvAttribution(
        tenant_id=tenant_id,
        external_order_id=unattributed_order.id,
        amount=50,
        original_amount=50,
        match_type="legacy_phone",
        scan_time=now - timedelta(days=2),
        attribution_window_hours=720,
        confidence_score=1,
        authority_status="legacy_quarantined",
    )
    db_session.add_all([confirmed_attribution, quarantined_attribution])
    await db_session.flush()
    db_session.add(
        GmvAttributionConfirmation(
            tenant_id=tenant_id,
                attribution_id=confirmed_attribution.id,
                external_order_id=confirmed_order.id,
                auth_session_id=uuid.uuid4(),
                actor_account_id=uuid.UUID(ACCOUNT_ID),
                consumer_id=confirmed_attribution.consumer_id,
            scan_event_id=uuid.uuid4(),
            scan_event_time=now - timedelta(days=2),
            scan_received_at=now - timedelta(days=2),
            visitor_id="trusted-visitor",
            public_id="trusted-code",
            window_started_at=now - timedelta(days=2),
            window_ends_at=now + timedelta(days=28),
            attribution_window_hours=720,
            idempotency_key="confirmed-attribution",
            payload_digest="a" * 64,
            provenance_digest="b" * 64,
            confirmed_at=now,
        )
    )
    db_session.add_all(
        [
            IntentEvent(
                tenant_id=tenant_id,
                event_type="page_view",
                client_event_id="trusted-receipt-clock",
                occurred_at=now - timedelta(days=365),
                received_at=now - timedelta(days=1),
            ),
            IntentEvent(
                tenant_id=tenant_id,
                event_type="page_view",
                client_event_id="outside-receipt-window",
                occurred_at=now - timedelta(days=1),
                received_at=now - timedelta(days=365),
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/conversion-funnel", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["report_type"] == "result_occurrence"
    assert body["intent_events"] == 1
    assert body["visitor_cohort"]["confirmed_orders"] == 1
    assert body["visitor_cohort"]["confirmed_net_amount"] == 100
    assert body["unattributed_order_count"] == 1
    assert body["order_data_quality"] == "incomplete"
    assert all(step["rate"] is None for step in body["steps"])
    assert next(step for step in body["steps"] if step["name"] == "订单总额")["unit"] == "yuan"


@pytest.mark.asyncio
async def test_scan_stats_default_range(client: AsyncClient):
    """默认返回最近 7 天数据"""
    resp = await client.get("/api/v1/analytics/scan-stats", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_scan_stats_with_date_range(client: AsyncClient):
    """指定日期范围"""
    resp = await client.get(
        "/api/v1/analytics/scan-stats",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_campaign_scan_stats(client: AsyncClient):
    """活动维度扫码统计（无 campaign_id 时返回空或全部）"""
    resp = await client.get(
        "/api/v1/analytics/campaign-scan-stats",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_campaign_scan_stats_with_campaign_id(client: AsyncClient):
    """指定不存在的 campaign_id 应返回空列表"""
    resp = await client.get(
        "/api/v1/analytics/campaign-scan-stats",
        params={"campaign_id": "019e887f-0000-7000-a000-000000000001"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_dashboard(client: AsyncClient):
    """仪表盘数据"""
    resp = await client.get("/api/v1/analytics/dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "today_scans" in data
    assert "cumulative_scans" in data


@pytest.mark.asyncio
async def test_conversion_funnel_returns_seven_layer_contract(client: AsyncClient):
    """yimatong-zgb1.14：漏斗返回 7 层结构（有效访问为分母）。

    旧契约：signup_count（留资）。
    新契约：7 层漏斗（有效访问/参与意图/权益确认/企微确认/订单/退款/净额）。
    """
    resp = await client.get("/api/v1/analytics/conversion-funnel", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    # yimatong-zgb1.14：7 层漏斗契约
    assert len(body["steps"]) == 7
    step_names = [s["name"] for s in body["steps"]]
    assert "有效访问" in step_names
    assert "净成交额" in step_names
    # 净额字段存在（AC5）
    assert "net_amount" in body
    assert "order_amount" in body
    assert "refund_amount" in body


@pytest.mark.asyncio
async def test_extended_dashboard_widgets_do_not_500(client: AsyncClient):
    """工作台扩展组件端点在空数据下也应返回可渲染结构。"""
    for endpoint, key in [
        ("/api/v1/analytics/alerts", "alerts"),
        ("/api/v1/analytics/recent-events", "events"),
    ]:
        resp = await client.get(endpoint, headers=_auth_headers())
        assert resp.status_code == 200
        assert key in resp.json()


@pytest.mark.asyncio
async def test_code_stats(client: AsyncClient):
    """码状态统计"""
    resp = await client.get("/api/v1/analytics/code-stats", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "by_status" in data
