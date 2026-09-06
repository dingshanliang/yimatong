"""A7-005: 扫码统计 API 测试"""

from collections.abc import AsyncGenerator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.analytics import DailyScanStats
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


class TestStatsClockDayBoundary:
    """统计日切 Asia/Shanghai：UTC 16:00 后的事件属于统计时区的次日。"""

    @pytest.mark.asyncio
    async def test_aggregate_buckets_by_shanghai_day(self, db_session: AsyncSession):
        import uuid
        from datetime import UTC, datetime

        from app.models.scan import ScanEvent
        from app.services.analytics import aggregate_daily_stats

        tid = uuid.uuid4()
        # 2026-09-05 17:00 UTC = 2026-09-06 01:00（上海）→ 统计日 2026-09-06
        boundary_event = ScanEvent(
            tenant_id=tid,
            public_id="TZ-BOUNDARY-1",
            scan_time=datetime(2026, 9, 5, 17, 0, tzinfo=UTC),
            is_first_scan=True,
            environment="wechat",
        )
        # 2026-09-05 15:00 UTC = 2026-09-05 23:00（上海）→ 统计日 2026-09-05
        same_day_event = ScanEvent(
            tenant_id=tid,
            public_id="TZ-SAMEDAY-1",
            scan_time=datetime(2026, 9, 5, 15, 0, tzinfo=UTC),
            is_first_scan=True,
            environment="wechat",
        )
        db_session.add_all([boundary_event, same_day_event])
        await db_session.commit()

        await aggregate_daily_stats(db_session, tid, date(2026, 9, 5))
        await aggregate_daily_stats(db_session, tid, date(2026, 9, 6))
        await db_session.commit()

        rows = {
            row.date: row.total_scans
            for row in (
                await db_session.execute(
                    select(DailyScanStats).where(DailyScanStats.tenant_id == tid)
                )
            )
            .scalars()
            .all()
        }
        assert rows[date(2026, 9, 5)] == 1
        assert rows[date(2026, 9, 6)] == 1
