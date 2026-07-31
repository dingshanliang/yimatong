"""上线门禁事实源与幂等执行测试。"""

import pytest

from app.services.launch import (
    build_launch_readiness,
    confirm_and_launch,
    create_launch_release,
    resume_launch_release,
    suspend_launch_release,
)
from app.services.page import create_page_version


@pytest.mark.anyio
async def test_readiness_uses_real_facts_not_onboarding_progress(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts

    snapshot, digest, _ = await build_launch_readiness(
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
async def test_readiness_rejects_unbound_campaign(db, launch_facts):
    tenant_id, _account_id, version, campaign, batch = launch_facts
    campaign.product_id = None
    await db.flush()

    snapshot, _digest, _ = await build_launch_readiness(
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

    launched = await confirm_and_launch(db, release, account_id, "release-2026-001")
    repeated = await confirm_and_launch(db, release, account_id, "release-2026-001")

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
    await confirm_and_launch(db, release, account_id, "release-2026-002")

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
    await confirm_and_launch(db, release, account_id, "release-2026-003")
    await suspend_launch_release(db, release, account_id, "发现活动配置需要复核")
    assert release.status == "suspended"

    await resume_launch_release(db, release, account_id)
    assert release.status == "live"
