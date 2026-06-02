"""统计 API 测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.main import app
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
async def test_code_stats(client: AsyncClient):
    """码状态统计"""
    resp = await client.get("/api/v1/analytics/code-stats", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "by_status" in data
