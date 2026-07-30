"""A7-005: 扫码统计 API 测试"""

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
async def auth_setup(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "统计测试",
            "admin_email": "stats@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 插入一些汇总数据
    import uuid

    for i in range(3):
        s = DailyScanStats(
            tenant_id=uuid.UUID(tid),
            date=date(2026, 5, 25 + i),
            total_scans=100 + i * 10,
            uv=50 + i * 5,
            first_scans=20 + i,
            rescans=80 + i * 9,
        )
        db_session.add(s)
    await db_session.commit()

    return tid, headers


class TestScanStatsAPI:
    @pytest.mark.anyio
    async def test_get_scan_stats(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.get(
            "/api/v1/analytics/scan-stats?start_date=2026-05-25&end_date=2026-05-27",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["total_scans"] == 100

    @pytest.mark.anyio
    async def test_default_date_range(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.get(
            "/api/v1/analytics/scan-stats",
            headers=headers,
        )
        assert resp.status_code == 200
