"""上线门禁事实源与幂等执行测试。"""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select, update

from app.models.campaign import Benefit
from app.models.connector import Connector
from app.models.launch import LaunchRelease
from app.models.product import BatchStatus, Brand, Product, ProductAsset, ProductionBatch
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.services.launch import (
    build_launch_readiness,
    confirm_launch_release,
    create_launch_release,
    launch_confirmed_release,
    record_launch_release_valid_scan,
    refresh_launch_release,
    resolve_current_launch_release,
    resume_launch_release,
    serialize_launch_release,
    suspend_launch_release,
)
from app.services.page import create_page_version
from app.services.scan_token import ScanLaunchAuthority


@pytest.mark.anyio
async def test_readiness_uses_real_facts_not_onboarding_progress(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts

    snapshot, digest, _, _manifest = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    assert snapshot["ready"] is True
    assert snapshot["passed_count"] == 4
    assert digest


@pytest.mark.anyio
async def test_readiness_uses_an_activated_sample_code_without_requiring_a_pre_live_scan(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts
    await db.execute(delete(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
    await db.flush()

    snapshot, _digest, _, _manifest = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    sample_check = next(item for item in snapshot["checks"] if item["key"] == "sample_code_ready")
    assert sample_check["passed"] is True
    assert "样本码" in sample_check["label"]
    assert snapshot["ready"] is True


@pytest.mark.anyio
async def test_sqlite_manifest_binds_display_and_claim_configuration_but_not_runtime_counters(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    brand = Brand(tenant_id=tenant_id, name="可见品牌", logo_url="https://cdn.example/brand.png")
    db.add_all(
        [
            Tenant(
                id=tenant_id,
                name="品牌租户",
                slug=f"launch-{tenant_id.hex[:8]}",
                brand_profile={"primary_color": "#123456"},
                enabled_features={"cash_red_packet": True},
            ),
            brand,
        ]
    )
    await db.flush()
    product = Product(
        id=campaign.product_id,
        tenant_id=tenant_id,
        brand_id=brand.id,
        name="可见产品",
        description="产品介绍",
        image_url="https://cdn.example/product.png",
        origin="中国",
    )
    connector = Connector(
        tenant_id=tenant_id,
        name="券连接器",
        connector_type="coupon_api",
        config={"endpoint_name": "brand-coupon"},
        secrets_encrypted=b"encrypted-secret",
        enabled=True,
    )
    db.add_all([product, connector])
    await db.flush()
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="首扫礼券",
        benefit_type="platform_coupon",
        config_json={"coupon_name": "夏日券", "claimed_budget": 0},
        connector_id=connector.id,
        stock_total=100,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    asset = ProductAsset(
        tenant_id=tenant_id,
        product_id=product.id,
        asset_type="test_report",
        name="质检报告",
        issuer="检测机构",
        valid_until=date(2027, 1, 1),
        file_url="https://cdn.example/report.pdf",
        status="active",
    )
    db.add_all([benefit, asset])
    await db.flush()

    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    manifest = release.readiness_manifest
    original_digest = release.content_digest

    assert manifest["campaign"]["name"] == campaign.name
    assert manifest["benefits"][0]["name"] == "首扫礼券"
    assert manifest["benefits"][0]["config"] == {"coupon_name": "夏日券"}
    assert manifest["benefits"][0]["connector_config"] == {"endpoint_name": "brand-coupon"}
    assert manifest["benefits"][0]["connector_secret_sha256"]
    assert "stock_used" not in manifest["benefits"][0]
    assert manifest["product"]["name"] == "可见产品"
    assert manifest["brand"]["name"] == "可见品牌"
    assert manifest["tenant_branding"]["brand_profile"] == {"primary_color": "#123456"}
    assert manifest["public_assets"][0]["name"] == "质检报告"
    assert manifest["production_batch"]["batch_code"] == "PB-LAUNCH-001"

    await confirm_launch_release(db, release, account_id, "confirm-canonical-manifest")
    await launch_confirmed_release(db, release, account_id, "launch-canonical-manifest")
    event = await db.scalar(
        select(ScanEvent).where(ScanEvent.tenant_id == tenant_id, ScanEvent.public_id == "LAUNCHCODE001")
    )
    from app.services.campaign import claim_benefit

    claimed = await claim_benefit(
        db,
        tenant_id,
        benefit.id,
        "consumer-canonical-manifest",
        "claim:v2:canonical-manifest",
        public_id="LAUNCHCODE001",
        scan_event_id=event.id,
        scanned_product_id=campaign.product_id,
        launch_authority=ScanLaunchAuthority(
            launch_release_id=release.id,
            campaign_id=campaign.id,
            code_batch_id=batch.id,
            content_digest=release.content_digest,
        ),
    )
    assert claimed["status"] == "success"
    benefit.config_json = {"coupon_name": "夏日券", "claimed_budget": 1}
    db.add(
        ScanEvent(
            tenant_id=tenant_id,
            public_id="LAUNCHCODE001",
            scan_time=datetime.now(UTC) + timedelta(seconds=1),
            is_valid_visit=True,
            environment="browser",
        )
    )
    await db.flush()
    await refresh_launch_release(db, release)
    assert release.content_digest == original_digest
    assert release.status == "live"

    benefit.name = "更名后的礼券"
    await db.flush()
    await refresh_launch_release(db, release)
    assert release.content_digest != original_digest


@pytest.mark.anyio
async def test_sqlite_resolver_invalidates_the_live_release_when_campaign_rules_drift(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-rules-drift")
    await launch_confirmed_release(db, release, account_id, "launch-rules-drift")

    campaign.rules_json = {"participation_condition_type": "member_only"}
    await db.flush()
    resolved = await resolve_current_launch_release(db, tenant_id, "LAUNCHCODE001")

    assert resolved is None
    assert release.status == "invalidated"
    assert release.failure_reason == "上线版本依赖项发生变化，需要重新确认"


@pytest.mark.anyio
async def test_serialize_launch_release_exposes_only_the_v3_sample_code_readiness(launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    sample_id = uuid.uuid4()
    release = LaunchRelease(
        tenant_id=tenant_id,
        page_template_id=version.page_template_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
        status="preparing",
        readiness_snapshot={"version": 3, "ready": True},
        readiness_manifest={
            "version": 3,
            "sample_code": {
                "id": str(sample_id),
                "public_id": "LAUNCHCODE001",
                "status": "activated",
                "code_type": "single",
                "code_batch_id": str(batch.id),
            },
            "benefits": [{"config": {"secret": "must-not-leak"}}],
        },
        readiness_code_item_id=sample_id,
        content_digest="a" * 64,
        created_by=account_id,
        created_by_tenant_id=tenant_id,
    )

    payload = serialize_launch_release(release)

    assert payload["readiness_sample_code"] == {
        "public_id": "LAUNCHCODE001",
        "status": "activated",
        "ready": True,
    }
    assert "readiness_manifest" not in payload


@pytest.mark.anyio
async def test_readiness_rejects_unbound_campaign(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts
    campaign.product_id = None
    await db.flush()

    snapshot, _digest, _, _manifest = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    campaign_check = next(item for item in snapshot["checks"] if item["key"] == "campaign_active")
    assert campaign_check["passed"] is False
    assert snapshot["ready"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("status", [BatchStatus.recalled, BatchStatus.expired])
async def test_readiness_rejects_non_active_production_batch(db, launch_facts, status):
    tenant_id, _account_id, version, campaign, batch = launch_facts
    production_batch = await db.get(ProductionBatch, batch.production_batch_id)
    production_batch.status = status
    if status == BatchStatus.recalled:
        production_batch.recall_reason = "safety recall"
        production_batch.recalled_at = datetime.now(UTC)
        production_batch.recalled_by = str(_account_id)
    await db.flush()

    snapshot, _digest, _, _manifest = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    batch_check = next(item for item in snapshot["checks"] if item["key"] == "code_batch_activated")
    assert batch_check["passed"] is False
    assert snapshot["ready"] is False


@pytest.mark.anyio
async def test_readiness_rejects_active_batch_with_past_expiry_date(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts
    production_batch = await db.get(ProductionBatch, batch.production_batch_id)
    production_batch.expiry_date = date.today() - timedelta(days=1)
    await db.flush()

    snapshot, _digest, _, _manifest = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    batch_check = next(item for item in snapshot["checks"] if item["key"] == "code_batch_activated")
    assert batch_check["passed"] is False
    assert snapshot["ready"] is False


@pytest.mark.anyio
async def test_confirm_and_launch_is_idempotent(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )

    await confirm_launch_release(db, release, account_id, "confirm-release-2026-001")
    launched = await launch_confirmed_release(db, release, account_id, "release-2026-001")
    repeated = await launch_confirmed_release(db, release, account_id, "release-2026-001")

    assert launched.id == repeated.id
    assert launched.status == "live"
    assert launched.brand_confirmed_by == account_id
    assert launched.launched_by == account_id


@pytest.mark.anyio
async def test_new_page_version_invalidates_confirmed_release(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-release-2026-002")
    await launch_confirmed_release(db, release, account_id, "release-2026-002")

    new_version = await create_page_version(
        db,
        tenant_id,
        version.page_template_id,
        {"dsl_version": "1.0", "title": "新版正式页"},
        account_id,
    )

    assert new_version["status"] == "draft"
    assert release.status == "invalidated"
    assert "新版本" in release.failure_reason


@pytest.mark.anyio
async def test_postgres_page_create_leaves_launch_invalidation_inside_page_authority(monkeypatch):
    tenant_id = uuid.uuid4()
    template_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    version_id = uuid.uuid4()
    authority_result = MagicMock()
    authority_result.mappings.return_value.one_or_none.return_value = {"page_version_id": version_id}
    db = AsyncMock()
    db.execute.return_value = authority_result
    loaded = {
        "id": str(version_id),
        "page_template_id": str(template_id),
        "status": "draft",
    }
    monkeypatch.setattr("app.core.database._session_uses_postgresql", lambda _db: True)
    monkeypatch.setattr("app.services.page._page_auth_session_id", lambda: uuid.uuid4())
    monkeypatch.setattr("app.services.page._load_page_version_dict", AsyncMock(return_value=loaded))

    result = await create_page_version(
        db,
        tenant_id,
        template_id,
        {"dsl_version": "1.0", "title": "新版本"},
        actor_id,
    )

    assert result == loaded
    assert db.execute.await_count == 1


@pytest.mark.anyio
async def test_suspend_requires_explicit_resume(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-release-2026-003")
    await launch_confirmed_release(db, release, account_id, "release-2026-003")
    await suspend_launch_release(db, release, account_id, "发现活动配置需要复核")
    assert release.status == "suspended"

    await resume_launch_release(db, release, account_id)
    assert release.status == "live"


@pytest.mark.anyio
async def test_authority_mutation_reloads_the_database_state_over_the_identity_map(db, launch_facts, monkeypatch):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    assert release.status == "pending_confirmation"

    async def fake_authority(_db, _sql, params):
        await _db.execute(
            update(LaunchRelease)
            .where(LaunchRelease.id == params["release_id"])
            .values(status="confirmed")
            .execution_options(synchronize_session=False)
        )
        return {"release_id": params["release_id"]}

    monkeypatch.setattr("app.services.launch._session_uses_postgresql", lambda _db: True)
    monkeypatch.setattr("app.services.launch._launch_authority_one", fake_authority)
    monkeypatch.setattr(
        "app.services.launch._authority_actor_params",
        lambda requested_tenant_id, action: {
            "tenant_id": requested_tenant_id,
            "auth_session_id": uuid.uuid4(),
            "audit_id": uuid.uuid4(),
            "action_id": uuid.uuid4(),
            "action": action,
        },
    )

    confirmed = await confirm_launch_release(db, release, account_id, "identity-map-confirm")

    assert confirmed is release
    assert confirmed.status == "confirmed"


@pytest.mark.anyio
async def test_live_release_observation_binds_only_an_exact_authoritative_valid_scan(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-observation")
    await launch_confirmed_release(db, release, account_id, "launch-observation")
    scan_time = datetime.now(UTC) + timedelta(seconds=1)
    event = ScanEvent(
        tenant_id=tenant_id,
        public_id="LAUNCHCODE001",
        scan_time=scan_time,
        is_valid_visit=True,
        environment="browser",
    )
    db.add(event)
    await db.flush()

    observed = await record_launch_release_valid_scan(
        db,
        tenant_id=tenant_id,
        release_id=release.id,
        scan_event_id=event.id,
        scan_time=scan_time,
    )
    later_scan_time = scan_time + timedelta(seconds=1)
    later_event = ScanEvent(
        tenant_id=tenant_id,
        public_id="LAUNCHCODE001",
        scan_time=later_scan_time,
        is_valid_visit=True,
        environment="browser",
    )
    db.add(later_event)
    await db.flush()
    already_observed = await record_launch_release_valid_scan(
        db,
        tenant_id=tenant_id,
        release_id=release.id,
        scan_event_id=later_event.id,
        scan_time=later_scan_time,
    )

    assert observed == {
        "release_id": release.id,
        "observation_status": "observed",
        "recorded_at": scan_time,
        "replayed": False,
    }
    assert already_observed == {
        "release_id": release.id,
        "observation_status": "already_observed",
        "recorded_at": scan_time,
        "replayed": True,
    }
    assert release.first_valid_scan_event_id == event.id
    assert release.first_valid_scan_time == scan_time


@pytest.mark.anyio
async def test_live_release_observation_rejects_a_non_authoritative_scan_fact(db, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-invalid-observation")
    await launch_confirmed_release(db, release, account_id, "launch-invalid-observation")
    scan_time = datetime.now(UTC) + timedelta(seconds=1)
    event = ScanEvent(
        tenant_id=tenant_id,
        public_id="LAUNCHCODE001",
        scan_time=scan_time,
        is_valid_visit=False,
        environment="browser",
    )
    db.add(event)
    await db.flush()

    with pytest.raises(RuntimeError, match="observation rejected"):
        await record_launch_release_valid_scan(
            db,
            tenant_id=tenant_id,
            release_id=release.id,
            scan_event_id=event.id,
            scan_time=scan_time,
        )

    assert release.first_valid_scan_event_id is None
