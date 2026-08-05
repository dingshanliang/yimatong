"""A1-009: 套餐额度字段与限制检查验收测试"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.code import CodeItem
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.services.entitlement import TenantPlanExpiredError, require_active_plan
from app.services.quota import (
    QuotaExceededError,
    check_quota,
    check_quota_incremental,
    check_quota_incremental_locked,
)
from tests.conftest import TestSessionLocal


class TestQuota:
    def test_quota_check_passes_within_limit(self):
        quota = {"max_codes": 1000, "max_campaigns": 10, "max_accounts": 5}
        check_quota(quota, "max_codes", 500)

    def test_quota_check_fails_at_limit(self):
        quota = {"max_codes": 1000, "max_campaigns": 10, "max_accounts": 5}
        import pytest

        with pytest.raises(QuotaExceededError):
            check_quota(quota, "max_codes", 1001)

    def test_quota_check_exact_limit_passes(self):
        quota = {"max_codes": 1000}
        check_quota(quota, "max_codes", 1000)

    def test_quota_exceeded_is_exception(self):
        assert issubclass(QuotaExceededError, Exception)

    def test_negative_limit_allows_unlimited_single_operation(self):
        check_quota({"max_codes_per_batch": -1}, "max_codes_per_batch", 1_000_000)

    def test_negative_limit_allows_unlimited_incremental_usage(self):
        check_quota_incremental({"max_accounts": -1}, "max_accounts", 10_000, 1)


@pytest.mark.anyio
async def test_expired_plan_is_reversible_without_changing_tenant_status():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        tenant = Tenant(
            id=tenant_id,
            name="到期租户",
            slug=f"expired-{tenant_id.hex[:8]}",
            plan_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        db.add(tenant)
        await db.flush()

        with pytest.raises(TenantPlanExpiredError):
            await require_active_plan(db, tenant_id)
        assert tenant.status == "active"

        tenant.plan_expires_at = datetime.now(UTC) + timedelta(days=1)
        assert await require_active_plan(db, tenant_id) is tenant


@pytest.mark.anyio
async def test_max_scans_counts_successful_rows_and_repeats():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="扫码配额租户",
                slug=f"scan-quota-{tenant_id.hex[:8]}",
                quota={"max_scans": 1},
            )
        )
        await db.flush()

        await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)
        db.add(
            ScanEvent(
                tenant_id=tenant_id,
                public_id="SAME-CODE",
                scan_time=datetime.now(UTC),
            )
        )
        await db.flush()

        with pytest.raises(QuotaExceededError, match="max_scans"):
            await check_quota_incremental_locked(db, tenant_id, "max_scans", ScanEvent)


@pytest.mark.anyio
async def test_max_codes_uses_existing_rows_plus_requested_amount():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="码量配额租户",
                slug=f"code-quota-{tenant_id.hex[:8]}",
                quota={"max_codes": 2},
            )
        )
        db.add(
            CodeItem(
                tenant_id=tenant_id,
                code_batch_id=uuid.uuid4(),
                public_id="EXISTING-CODE",
            )
        )
        await db.flush()

        await check_quota_incremental_locked(db, tenant_id, "max_codes", CodeItem, 1)
        with pytest.raises(QuotaExceededError, match="max_codes"):
            await check_quota_incremental_locked(db, tenant_id, "max_codes", CodeItem, 2)
