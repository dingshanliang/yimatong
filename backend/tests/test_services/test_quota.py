"""A1-009: 套餐额度字段与限制检查验收测试"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.campaign import Campaign
from app.models.code import CodeItem
from app.models.plan import TenantQuotaUsage
from app.models.product import Brand, Product
from app.models.scan import ScanEvent
from app.models.tenant import Account, Organization, Tenant
from app.services.campaign import create_campaign
from app.services.entitlement import TenantPlanExpiredError, require_active_plan
from app.services.organization import create_account
from app.services.product import create_product
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    CumulativeQuotaKey,
    InvalidQuotaConfigurationError,
    QuotaExceededError,
    QuotaReconciliationGateError,
    check_quota,
    check_quota_incremental,
    check_quota_incremental_locked,
    reconcile_quota_usage_from_authoritative_rows,
    reserve_quota,
    validate_quota_config,
)
from tests.conftest import TestSessionLocal


def _ready_usage(tenant_id: uuid.UUID, **counters: int) -> TenantQuotaUsage:
    return TenantQuotaUsage(
        tenant_id=tenant_id,
        reconciled_at=datetime.now(UTC),
        source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
        enforcement_ready=True,
        **counters,
    )


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

    @pytest.mark.parametrize(
        "quota",
        [
            {"max_active_campaigns": 1},
            {"unknown": 1},
            {"max_codes": True},
            {"max_codes": 1.5},
            {"max_codes": -2},
        ],
    )
    def test_invalid_quota_documents_fail_closed(self, quota):
        with pytest.raises(InvalidQuotaConfigurationError):
            validate_quota_config(quota)

    def test_max_products_is_a_supported_cumulative_quota(self):
        assert validate_quota_config({"max_products": 2}) == {"max_products": 2}
        check_quota_incremental({"max_products": 2}, CumulativeQuotaKey.MAX_PRODUCTS, 1, 1)

    def test_operation_only_quota_is_not_accepted_as_cumulative(self):
        with pytest.raises(InvalidQuotaConfigurationError):
            check_quota_incremental({"max_codes_per_batch": 10}, "max_codes_per_batch", 0, 1)


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
        db.add(_ready_usage(tenant_id))
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
        db.add(_ready_usage(tenant_id, codes=1))
        await db.flush()

        await check_quota_incremental_locked(db, tenant_id, "max_codes", CodeItem, 1)
        with pytest.raises(QuotaExceededError, match="max_codes"):
            await check_quota_incremental_locked(db, tenant_id, "max_codes", CodeItem, 2)


@pytest.mark.anyio
async def test_reservation_rollback_releases_usage():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="回滚配额租户",
                slug=f"rollback-quota-{tenant_id.hex[:8]}",
                quota={"max_products": 1},
            )
        )
        await db.commit()

        await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
        await db.rollback()

        usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
        assert usage.products == 1


@pytest.mark.anyio
async def test_bridge_counter_is_maintained_without_rejecting_until_reconciled():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="桥接期配额租户",
                slug=f"bridge-quota-{tenant_id.hex[:8]}",
                quota={"max_products": 0},
            )
        )
        db.add(TenantQuotaUsage(tenant_id=tenant_id, products=9, enforcement_ready=False))
        await db.flush()

        usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
        assert usage.products == 10
        assert usage.enforcement_ready is False


@pytest.mark.anyio
async def test_canonical_product_service_enforces_quota_without_http_route():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        tenant = Tenant(
            id=tenant_id,
            name="产品服务配额租户",
            slug=f"product-service-quota-{tenant_id.hex[:8]}",
            quota={"max_products": 0},
        )
        brand = Brand(tenant_id=tenant_id, name="测试品牌")
        db.add_all([tenant, brand, _ready_usage(tenant_id)])
        await db.flush()

        with pytest.raises(QuotaExceededError, match="max_products"):
            await create_product(db, tenant_id, brand.id, "超额产品")
        assert await db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)) == 0


@pytest.mark.anyio
async def test_canonical_campaign_service_enforces_quota_without_http_route():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="活动服务配额租户",
                slug=f"campaign-service-quota-{tenant_id.hex[:8]}",
                quota={"max_campaigns": 0},
            )
        )
        db.add(_ready_usage(tenant_id))
        await db.flush()

        with pytest.raises(QuotaExceededError, match="max_campaigns"):
            await create_campaign(
                db,
                tenant_id,
                "超额活动",
                "coupon",
                "2026-08-01T00:00:00Z",
                "2026-08-31T00:00:00Z",
                {},
            )
        assert await db.scalar(select(func.count()).select_from(Campaign).where(Campaign.tenant_id == tenant_id)) == 0


@pytest.mark.anyio
async def test_canonical_account_service_enforces_quota_without_http_route():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        tenant = Tenant(
            id=tenant_id,
            name="账号服务配额租户",
            slug=f"account-service-quota-{tenant_id.hex[:8]}",
            quota={"max_accounts": 0},
        )
        organization = Organization(tenant_id=tenant_id, name="总部")
        db.add_all([tenant, organization, _ready_usage(tenant_id)])
        await db.flush()

        with pytest.raises(QuotaExceededError, match="max_accounts"):
            await create_account(
                db,
                tenant_id,
                organization.id,
                "blocked@example.com",
                "超额账号",
                "Password123",
            )
        assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)) == 0


@pytest.mark.anyio
async def test_controlled_reconciliation_matches_all_authoritative_fact_tables():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        tenant = Tenant(id=tenant_id, name="重算租户", slug=f"reconcile-{tenant_id.hex[:8]}")
        brand = Brand(tenant_id=tenant_id, name="重算品牌")
        organization = Organization(tenant_id=tenant_id, name="重算组织")
        db.add_all([tenant, brand, organization])
        await db.flush()
        db.add_all(
            [
                Product(tenant_id=tenant_id, brand_id=brand.id, name="重算产品"),
                Campaign(
                    tenant_id=tenant_id,
                    name="重算活动",
                    campaign_type="coupon",
                    start_at=datetime(2026, 8, 1, tzinfo=UTC),
                    end_at=datetime(2026, 8, 31, tzinfo=UTC),
                    rules_json={},
                ),
                Account(
                    tenant_id=tenant_id,
                    organization_id=organization.id,
                    email="reconcile@example.com",
                    hashed_password="hashed",
                    name="重算账号",
                ),
                CodeItem(tenant_id=tenant_id, code_batch_id=uuid.uuid4(), public_id="RECONCILE-CODE"),
                ScanEvent(tenant_id=tenant_id, public_id="RECONCILE-CODE", scan_time=datetime.now(UTC)),
                TenantQuotaUsage(
                    tenant_id=tenant_id,
                    codes=99,
                    scans=99,
                    campaigns=99,
                    products=99,
                    accounts=99,
                ),
            ]
        )
        await db.flush()

        with pytest.raises(QuotaReconciliationGateError):
            await reconcile_quota_usage_from_authoritative_rows(db, tenant_id)
        usage = await reconcile_quota_usage_from_authoritative_rows(db, tenant_id, old_writers_drained=True)
        assert (usage.codes, usage.scans, usage.campaigns, usage.products, usage.accounts) == (1, 1, 1, 1, 1)
        assert usage.enforcement_ready is True
        assert usage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
        assert usage.reconciled_at is not None
