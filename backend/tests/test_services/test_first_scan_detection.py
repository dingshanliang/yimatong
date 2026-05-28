"""A6-005/A6-006: 扫码事件与首扫判断测试"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scan_event import _check_first_scan, record_scan_event
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db():
    async with TestSessionLocal() as session:
        yield session


class TestRecordScanEvent:
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
