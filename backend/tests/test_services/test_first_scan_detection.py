"""A6-005/A6-006: 扫码事件与首扫判断测试"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scan_event import _check_first_scan, record_scan_event


@pytest.fixture(autouse=True)
def allow_scan_quota(monkeypatch):
    monkeypatch.setattr("app.services.scan_event.check_quota_incremental_locked", AsyncMock())


class TestRecordScanEvent:
    @pytest.mark.anyio
    async def test_postgresql_uses_first_scan_authority_and_consumes_its_result(self):
        tenant_id = uuid.uuid4()
        first_scanned_at = datetime(2026, 8, 11, 4, 20, tzinfo=UTC)
        result = MagicMock()
        result.mappings.return_value.one.return_value = {
            "code_item_id": uuid.uuid4(),
            "first_scan": True,
            "first_scanned_at": first_scanned_at,
        }
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch("app.core.database._session_uses_postgresql", return_value=True),
            patch("app.services.scan_event.check_quota_incremental_locked", AsyncMock()),
            patch("app.services.scan_event.event_bus.emit", AsyncMock()),
        ):
            event = await record_scan_event(db, tenant_id, "PGFIRSTSCAN01")

        statement, parameters = db.execute.await_args.args
        assert "public.mark_code_item_first_scanned" in str(statement)
        assert parameters == {"tenant_id": tenant_id, "public_id": "PGFIRSTSCAN01"}
        assert event.is_first_scan is True
        assert event.scan_time == first_scanned_at

    @pytest.mark.anyio
    async def test_record_first_scan(self, db: AsyncSession):
        event = await record_scan_event(
            db,
            tenant_id=uuid.uuid4(),
            public_id="ABC123",
            ip_hash="abc123hash",
            user_agent="Mozilla/5.0",
            environment="browser",
        )
        assert event.id is not None
        assert event.public_id == "ABC123"
        assert event.is_first_scan is True
        assert event.environment == "browser"

    @pytest.mark.anyio
    async def test_second_scan_not_first(self, db: AsyncSession):
        tid = uuid.uuid4()
        await record_scan_event(db, tid, "DEF456")
        event2 = await record_scan_event(db, tid, "DEF456")
        assert event2.is_first_scan is False


class TestCheckFirstScan:
    @pytest.mark.anyio
    async def test_no_previous_events(self, db: AsyncSession):
        assert await _check_first_scan(db, "NEWCODE") is True

    @pytest.mark.anyio
    async def test_has_previous_events(self, db: AsyncSession):
        await record_scan_event(db, uuid.uuid4(), "EXISTCODE")
        assert await _check_first_scan(db, "EXISTCODE") is False
