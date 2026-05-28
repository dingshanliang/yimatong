"""A2-006: 审计日志 service 测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit import query_audit_logs, write_audit_log
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


class TestAuditLogService:
    @pytest.mark.anyio
    async def test_write_audit_log(self, db: AsyncSession):
        log = await write_audit_log(db, "op-1", "tenant-1", "create", "tenants/new")
        assert log.operator_id == "op-1"
        assert log.target_tenant_id == "tenant-1"
        assert log.action == "create"
        assert log.resource == "tenants/new"
        assert log.timestamp is not None

    @pytest.mark.anyio
    async def test_query_audit_logs_with_time_filter(self, db: AsyncSession):
        now = datetime.now(UTC)
        await write_audit_log(db, "op-1", "t-1", "read", "data")
        logs = await query_audit_logs(db, start_time=now - timedelta(hours=1))
        assert len(logs) >= 1

    @pytest.mark.anyio
    async def test_audit_log_fields(self, db: AsyncSession):
        log = await write_audit_log(db, "op-2", "t-2", "delete", "accounts/123")
        assert log.id is not None
        assert log.operator_id == "op-2"
        assert log.target_tenant_id == "t-2"
        assert log.action == "delete"
        assert log.resource == "accounts/123"
