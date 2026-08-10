"""客户正式上线门禁：准备度、确认、幂等执行和状态审计。"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignStatus
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus
from app.models.launch import LaunchRelease, LaunchReleaseStatus
from app.models.page import PageTemplate, PageVersion, PageVersionStatus
from app.models.product import ProductionBatch
from app.models.scan import ScanEvent
from app.services.audit import write_audit_log
from app.services.product import is_production_batch_effectively_active
from app.services.takeover import build_takeover_launch_gate_check


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check(key: str, label: str, passed: bool, detail: str) -> dict:
    return {"key": key, "label": label, "passed": passed, "detail": detail}


async def build_launch_readiness(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page_version_id: uuid.UUID,
    campaign_id: uuid.UUID,
    code_batch_id: uuid.UUID,
) -> tuple[dict, str, PageVersion | None]:
    """重新计算上线事实，不读取 onboarding_progress，也不接受前端勾选结果。"""
    page_result = await db.execute(
        select(PageVersion, PageTemplate)
        .join(PageTemplate, PageVersion.page_template_id == PageTemplate.id)
        .where(PageVersion.id == page_version_id, PageVersion.tenant_id == tenant_id)
    )
    page_row = page_result.first()
    page_version = page_row[0] if page_row else None
    page_template = page_row[1] if page_row else None

    campaign = await db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))
    code_batch = await db.scalar(
        select(CodeBatch).where(CodeBatch.id == code_batch_id, CodeBatch.tenant_id == tenant_id)
    )
    production_batch = None
    if code_batch and code_batch.production_batch_id:
        production_batch = await db.scalar(
            select(ProductionBatch).where(
                ProductionBatch.id == code_batch.production_batch_id,
                ProductionBatch.tenant_id == tenant_id,
                ProductionBatch.product_id == code_batch.product_id,
                ProductionBatch.sku_id == code_batch.sku_id,
            )
        )

    page_product_id = page_template.product_id if page_template else None
    page_passed = bool(
        page_version
        and page_template
        and page_version.status == PageVersionStatus.published
        and code_batch
        and page_product_id
        and page_product_id == code_batch.product_id
    )
    campaign_passed = bool(
        campaign
        and campaign.status == CampaignStatus.ACTIVE
        and code_batch
        and campaign.product_id
        and campaign.product_id == code_batch.product_id
    )
    batch_passed = bool(
        code_batch
        and code_batch.status == CodeBatchStatus.activated
        and production_batch
        and is_production_batch_effectively_active(production_batch)
    )

    scan_passed = False
    if code_batch:
        item_result = await db.scalars(
            select(CodeItem.public_id).where(
                CodeItem.tenant_id == tenant_id,
                CodeItem.code_batch_id == code_batch.id,
                CodeItem.status.in_((CodeItemStatus.activated, CodeItemStatus.bound)),
            )
        )
        public_ids = list(item_result)
        if public_ids:
            scan_passed = bool(
                await db.scalar(
                    select(ScanEvent.id)
                    .where(
                        ScanEvent.tenant_id == tenant_id,
                        ScanEvent.public_id.in_(public_ids),
                        ScanEvent.is_valid_visit.is_(True),
                    )
                    .limit(1)
                )
            )

    checks = [
        _check(
            "page_published",
            "扫码页已发布且绑定正确产品",
            page_passed,
            "页面已发布，且页面产品与码批次一致" if page_passed else "请先发布扫码页，并确认它绑定了本次上线的产品",
        ),
        _check(
            "campaign_active",
            "上线活动已启用且绑定正确产品",
            campaign_passed,
            "活动已启用，且活动产品与码批次一致"
            if campaign_passed
            else "请启用本次上线活动，并确认它绑定了本次上线的产品",
        ),
        _check(
            "code_batch_activated",
            "至少一个码批次已激活",
            batch_passed,
            "码批次已激活且生产批次有效" if batch_passed else "请确认码批次已激活，且关联生产批次仍为有效状态",
        ),
        _check(
            "scan_path_verified",
            "真实扫码路径验证通过",
            scan_passed,
            "已有有效扫码访问记录" if scan_passed else "请用真实码完成一次扫码，并确认扫码页正常打开",
        ),
    ]
    takeover_check = await build_takeover_launch_gate_check(db, tenant_id)
    if takeover_check:
        checks.append(takeover_check)
    snapshot = {
        "version": 1,
        "tenant_id": str(tenant_id),
        "page_version_id": str(page_version_id),
        "campaign_id": str(campaign_id),
        "code_batch_id": str(code_batch_id),
        "checks": checks,
        "passed_count": sum(1 for item in checks if item["passed"]),
        "total_count": len(checks),
        "ready": all(item["passed"] for item in checks),
        "calculated_at": datetime.now(UTC).isoformat(),
    }
    digest = _digest({key: value for key, value in snapshot.items() if key != "calculated_at"})
    return snapshot, digest, page_version


def serialize_launch_release(release: LaunchRelease) -> dict:
    snapshot = release.readiness_snapshot or {}
    return {
        "id": release.id,
        "tenant_id": release.tenant_id,
        "page_template_id": release.page_template_id,
        "page_version_id": release.page_version_id,
        "campaign_id": release.campaign_id,
        "code_batch_id": release.code_batch_id,
        "status": release.status.value if hasattr(release.status, "value") else release.status,
        "ready": bool(snapshot.get("ready")),
        "readiness_snapshot": snapshot,
        "content_digest": release.content_digest,
        "brand_confirmed_by": release.brand_confirmed_by,
        "brand_confirmed_at": release.brand_confirmed_at,
        "launched_by": release.launched_by,
        "launched_at": release.launched_at,
        "failure_reason": release.failure_reason,
        "suspension_reason": release.suspension_reason,
    }


async def get_launch_release(db: AsyncSession, tenant_id: uuid.UUID, release_id: uuid.UUID) -> LaunchRelease | None:
    return await db.scalar(
        select(LaunchRelease).where(LaunchRelease.id == release_id, LaunchRelease.tenant_id == tenant_id)
    )


async def refresh_launch_release(db: AsyncSession, release: LaunchRelease) -> LaunchRelease:
    snapshot, digest, _ = await build_launch_readiness(
        db,
        release.tenant_id,
        page_version_id=release.page_version_id,
        campaign_id=release.campaign_id,
        code_batch_id=release.code_batch_id,
    )
    release.readiness_snapshot = snapshot
    release.content_digest = digest
    if release.status in (LaunchReleaseStatus.confirmed, LaunchReleaseStatus.live) and (
        release.brand_confirmation_digest != digest
    ):
        release.status = LaunchReleaseStatus.invalidated
        release.failure_reason = "上线版本依赖项发生变化，需要重新确认"
    elif release.status == LaunchReleaseStatus.preparing:
        release.status = (
            LaunchReleaseStatus.pending_confirmation if snapshot["ready"] else LaunchReleaseStatus.preparing
        )
    await db.flush()
    return release


async def create_launch_release(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    page_version_id: uuid.UUID,
    campaign_id: uuid.UUID,
    code_batch_id: uuid.UUID,
) -> LaunchRelease:
    snapshot, digest, page_version = await build_launch_readiness(
        db,
        tenant_id,
        page_version_id=page_version_id,
        campaign_id=campaign_id,
        code_batch_id=code_batch_id,
    )
    if not page_version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="扫码页版本不存在或不属于当前租户")
    existing = await db.scalar(
        select(LaunchRelease).where(
            LaunchRelease.tenant_id == tenant_id,
            LaunchRelease.page_version_id == page_version_id,
            LaunchRelease.campaign_id == campaign_id,
            LaunchRelease.code_batch_id == code_batch_id,
            LaunchRelease.status.in_(
                (
                    LaunchReleaseStatus.preparing,
                    LaunchReleaseStatus.pending_confirmation,
                    LaunchReleaseStatus.confirmed,
                    LaunchReleaseStatus.live,
                )
            ),
        )
    )
    if existing:
        return await refresh_launch_release(db, existing)
    release = LaunchRelease(
        tenant_id=tenant_id,
        page_template_id=page_version.page_template_id,
        page_version_id=page_version_id,
        campaign_id=campaign_id,
        code_batch_id=code_batch_id,
        status=LaunchReleaseStatus.pending_confirmation if snapshot["ready"] else LaunchReleaseStatus.preparing,
        readiness_snapshot=snapshot,
        content_digest=digest,
        created_by=account_id,
    )
    db.add(release)
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "launch_release_created",
        f"launch_release:{release.id}",
        {"content_digest": digest, "ready": snapshot["ready"]},
    )
    return release


async def confirm_launch_release(db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID) -> LaunchRelease:
    await refresh_launch_release(db, release)
    if not release.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线准备度未通过，暂时不能确认")
    release.status = LaunchReleaseStatus.confirmed
    release.brand_confirmed_by = account_id
    release.brand_confirmed_at = datetime.now(UTC)
    release.brand_confirmation_digest = release.content_digest
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(release.tenant_id),
        "launch_release_confirmed",
        f"launch_release:{release.id}",
        {"content_digest": release.content_digest},
    )
    return release


async def confirm_and_launch(
    db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID, idempotency_key: str
) -> LaunchRelease:
    existing = await db.scalar(
        select(LaunchRelease).where(
            LaunchRelease.tenant_id == release.tenant_id,
            LaunchRelease.idempotency_key == idempotency_key,
        )
    )
    if existing and existing.id != release.id:
        return existing
    if release.status == LaunchReleaseStatus.live and release.idempotency_key == idempotency_key:
        return release
    await confirm_launch_release(db, release, account_id)
    release.idempotency_key = idempotency_key
    release.status = LaunchReleaseStatus.live
    release.launched_by = account_id
    release.launched_at = datetime.now(UTC)
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(release.tenant_id),
        "launch_release_launched",
        f"launch_release:{release.id}",
        {"content_digest": release.content_digest, "idempotency_key": idempotency_key},
    )
    return release


async def launch_confirmed_release(
    db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID, idempotency_key: str
) -> LaunchRelease:
    """代运营在已完成品牌确认后执行发布；不会替品牌方确认。"""
    existing = await db.scalar(
        select(LaunchRelease).where(
            LaunchRelease.tenant_id == release.tenant_id,
            LaunchRelease.idempotency_key == idempotency_key,
        )
    )
    if existing and existing.id != release.id:
        return existing
    if release.status == LaunchReleaseStatus.live and release.idempotency_key == idempotency_key:
        return release
    await refresh_launch_release(db, release)
    if release.status != LaunchReleaseStatus.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="品牌方尚未确认当前上线版本")
    if release.brand_confirmation_digest != release.content_digest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线版本已变化，需要品牌方重新确认")
    release.idempotency_key = idempotency_key
    release.status = LaunchReleaseStatus.live
    release.launched_by = account_id
    release.launched_at = datetime.now(UTC)
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(release.tenant_id),
        "launch_release_launched_by_agency",
        f"launch_release:{release.id}",
        {"content_digest": release.content_digest, "idempotency_key": idempotency_key},
    )
    return release


async def invalidate_launch_releases_for_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page_template_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> int:
    """新建页面版本会使该模板现有的确认失效，避免旧确认覆盖新版本。"""
    result = await db.execute(
        select(LaunchRelease).where(
            LaunchRelease.tenant_id == tenant_id,
            LaunchRelease.page_template_id == page_template_id,
            LaunchRelease.status.in_((LaunchReleaseStatus.confirmed, LaunchReleaseStatus.live)),
        )
    )
    releases = list(result.scalars().all())
    for release in releases:
        release.status = LaunchReleaseStatus.invalidated
        release.failure_reason = "页面产生了新版本，需要重新准备并确认"
        await write_audit_log(
            db,
            str(actor_id),
            str(tenant_id),
            "launch_release_invalidated",
            f"launch_release:{release.id}",
            {"reason": "page_version_created", "page_template_id": str(page_template_id)},
        )
    if releases:
        await db.flush()
    return len(releases)


async def suspend_launch_release(
    db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID, reason: str
) -> LaunchRelease:
    if release.status != LaunchReleaseStatus.live:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有已正式上线版本可以暂停")
    release.status = LaunchReleaseStatus.suspended
    release.suspended_by = account_id
    release.suspended_at = datetime.now(UTC)
    release.suspension_reason = reason
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(release.tenant_id),
        "launch_release_suspended",
        f"launch_release:{release.id}",
        {"reason": reason},
    )
    return release


async def resume_launch_release(db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID) -> LaunchRelease:
    if release.status != LaunchReleaseStatus.suspended:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有已暂停版本可以恢复")
    await refresh_launch_release(db, release)
    if not release.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线准备度已变化，需要重新确认")
    if release.brand_confirmation_digest != release.content_digest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线版本已变化，需要重新确认")
    release.status = LaunchReleaseStatus.live
    release.suspended_by = None
    release.suspended_at = None
    release.suspension_reason = None
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(release.tenant_id),
        "launch_release_resumed",
        f"launch_release:{release.id}",
        {"content_digest": release.content_digest},
    )
    return release
