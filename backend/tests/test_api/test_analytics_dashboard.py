"""A7-007: 统计看板 API 测试"""

from collections.abc import AsyncGenerator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.analytics import DailyScanStats
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

    import uuid

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
    async def test_dashboard_returns_aggregated_data(
        self, client: AsyncClient, dashboard_setup
    ):
        _, headers = dashboard_setup
        resp = await client.get(
            "/api/v1/analytics/dashboard", headers=headers,
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
    async def test_dashboard_empty_data(
        self, client: AsyncClient
    ):
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
            "/api/v1/analytics/dashboard", headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["today_scans"] == 0
        assert data["cumulative_scans"] == 0
