"""真实 PostgreSQL 上的租户行锁配额并发证明。"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.services.quota import QuotaExceededError, check_quota_incremental_locked


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_max_scans_concurrent_requests_cannot_both_cross_limit(migrated_pg_url: str):
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    async with factory() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        db.add(
            Tenant(
                id=tenant_id,
                name="并发扫码配额租户",
                slug=f"scan-concurrency-{tenant_id.hex[:8]}",
                quota={"max_scans": 1},
            )
        )
        await db.commit()

    first_has_lock = asyncio.Event()

    async def first_request() -> str:
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)
            first_has_lock.set()
            await asyncio.sleep(0.1)
            db.add(
                ScanEvent(
                    tenant_id=tenant_id,
                    public_id="CONCURRENT-1",
                    scan_time=datetime.now(UTC),
                )
            )
        return "accepted"

    async def second_request() -> str:
        await first_has_lock.wait()
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            try:
                await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)
            except QuotaExceededError:
                return "rejected"
            db.add(
                ScanEvent(
                    tenant_id=tenant_id,
                    public_id="CONCURRENT-2",
                    scan_time=datetime.now(UTC),
                )
            )
        return "accepted"

    try:
        assert await asyncio.gather(first_request(), second_request()) == ["accepted", "rejected"]
    finally:
        await engine.dispose()
