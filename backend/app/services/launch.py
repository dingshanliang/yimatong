"""客户正式上线门禁：准备度、确认、幂等执行和状态审计。"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import TypedDict

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, get_request_security_credential
from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus
from app.models.connector import Connector
from app.models.launch import LaunchRelease, LaunchReleaseStatus
from app.models.page import PageTemplate, PageVersion, PageVersionStatus
from app.models.product import Brand, Product, ProductAsset, ProductAssetStatus, ProductAssetType, ProductionBatch
from app.models.takeover import TakeoverProject
from app.models.tenant import Tenant
from app.services.audit import write_audit_log
from app.services.product import is_production_batch_effectively_active
from app.services.takeover import build_takeover_launch_gate_check
from app.utils import china_business_date


def _auth_session_id() -> uuid.UUID:
    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=401, detail="Live login session required for launch changes")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid login session") from exc


def _map_launch_db_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "pgcode", None
    )
    if sqlstate == "42501":
        return HTTPException(status_code=403, detail="Launch authority denied")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="Launch release not found")
    if sqlstate == "22023":
        return HTTPException(status_code=422, detail="Launch request is invalid")
    if sqlstate in {"23514", "23505"}:
        return HTTPException(status_code=409, detail="Launch state conflicts with the request")
    if sqlstate == "55P03":
        return HTTPException(status_code=409, detail="Launch authority is busy; retry", headers={"Retry-After": "1"})
    return None


async def _launch_authority_one(db: AsyncSession, sql: str, params: dict) -> dict:
    try:
        row = (await db.execute(text(sql), params)).mappings().one()
    except DBAPIError as exc:
        mapped = _map_launch_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return dict(row)


def _authority_actor_params(tenant_id: uuid.UUID, action: str) -> dict:
    return {
        "tenant_id": tenant_id,
        "auth_session_id": _auth_session_id(),
        "audit_id": uuid7(),
        "action_id": uuid7(),
        "action": action,
    }


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check(key: str, label: str, passed: bool, detail: str) -> dict:
    return {"key": key, "label": label, "passed": passed, "detail": detail}


def _manifest_value(value):
    if isinstance(value, (date, datetime, uuid.UUID)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    return value.value if hasattr(value, "value") else value


async def _build_canonical_launch_manifest(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page_version: PageVersion | None,
    page_template: PageTemplate | None,
    campaign: Campaign | None,
    code_batch: CodeBatch | None,
    production_batch: ProductionBatch | None,
    sample_code: CodeItem | None,
    ready: bool,
) -> dict:
    """Mirror compute_launch_readiness's brand-controlled manifest for SQLite tests."""
    product = None
    brand = None
    if code_batch is not None:
        product = await db.scalar(
            select(Product).where(Product.tenant_id == tenant_id, Product.id == code_batch.product_id)
        )
    if product is not None:
        brand = await db.scalar(select(Brand).where(Brand.tenant_id == tenant_id, Brand.id == product.brand_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))

    benefits = []
    if campaign is not None:
        benefit_rows = list(
            await db.scalars(
                select(Benefit)
                .where(Benefit.tenant_id == tenant_id, Benefit.campaign_id == campaign.id)
                .order_by(Benefit.id)
            )
        )
        connector_ids = {item.connector_id for item in benefit_rows if item.connector_id is not None}
        connectors = {
            item.id: item
            for item in (
                list(
                    await db.scalars(
                        select(Connector).where(Connector.tenant_id == tenant_id, Connector.id.in_(connector_ids))
                    )
                )
                if connector_ids
                else []
            )
        }
        for benefit in benefit_rows:
            connector = connectors.get(benefit.connector_id)
            config = dict(benefit.config_json or {})
            config.pop("claimed_budget", None)
            benefits.append(
                {
                    "id": str(benefit.id),
                    "name": benefit.name,
                    "type": benefit.benefit_type,
                    "status": _manifest_value(benefit.status),
                    "config": config,
                    "stock_total": benefit.stock_total,
                    "per_person_limit": benefit.per_person_limit,
                    "connector_id": str(benefit.connector_id) if benefit.connector_id else None,
                    "connector_type": connector.connector_type if connector else None,
                    "connector_enabled": connector.enabled if connector else None,
                    "connector_config": connector.config if connector else None,
                    "connector_secret_sha256": (
                        hashlib.sha256(connector.secrets_encrypted).hexdigest()
                        if connector and connector.secrets_encrypted is not None
                        else None
                    ),
                }
            )

    assets = []
    if code_batch is not None:
        assets = list(
            await db.scalars(
                select(ProductAsset)
                .where(
                    ProductAsset.tenant_id == tenant_id,
                    ProductAsset.product_id == code_batch.product_id,
                    ProductAsset.status == ProductAssetStatus.active,
                    ProductAsset.asset_type.in_((ProductAssetType.test_report, ProductAssetType.certificate)),
                    (ProductAsset.valid_until.is_(None) | (ProductAsset.valid_until >= china_business_date())),
                )
                .order_by(ProductAsset.id)
            )
        )
    takeovers = list(
        await db.scalars(
            select(TakeoverProject).where(TakeoverProject.tenant_id == tenant_id).order_by(TakeoverProject.id)
        )
    )

    return {
        "version": 3,
        "tenant_id": str(tenant_id),
        "page": {
            "template_id": str(page_template.id) if page_template else None,
            "template_status": _manifest_value(page_template.status) if page_template else None,
            "product_id": str(page_template.product_id) if page_template and page_template.product_id else None,
            "version_id": str(page_version.id) if page_version else None,
            "version_number": page_version.version if page_version else None,
            "version_status": _manifest_value(page_version.status) if page_version else None,
            "config": page_version.config_json if page_version else None,
            "published_at": _manifest_value(page_version.published_at) if page_version else None,
        },
        "campaign": {
            "id": str(campaign.id) if campaign else None,
            "name": campaign.name if campaign else None,
            "product_id": str(campaign.product_id) if campaign and campaign.product_id else None,
            "status": _manifest_value(campaign.status) if campaign else None,
            "start_at": _manifest_value(campaign.start_at) if campaign else None,
            "end_at": _manifest_value(campaign.end_at) if campaign else None,
            "rules": campaign.rules_json if campaign else None,
        },
        "benefits": benefits,
        "product": {
            "id": str(product.id) if product else None,
            "brand_id": str(product.brand_id) if product else None,
            "name": product.name if product else None,
            "description": product.description if product else None,
            "image_url": product.image_url if product else None,
            "origin": product.origin if product else None,
            "status": _manifest_value(product.status) if product else None,
        },
        "brand": {
            "id": str(brand.id) if brand else None,
            "name": brand.name if brand else None,
            "logo_url": brand.logo_url if brand else None,
            "status": _manifest_value(brand.status) if brand else None,
        },
        "tenant_branding": {
            "brand_profile": tenant.brand_profile if tenant else None,
            "enabled_features": tenant.enabled_features if tenant else None,
        },
        "public_assets": [
            {
                "id": str(asset.id),
                "type": _manifest_value(asset.asset_type),
                "name": asset.name,
                "description": asset.description,
                "issuer": asset.issuer,
                "valid_until": _manifest_value(asset.valid_until),
                "file_url": asset.file_url,
                "image_url": asset.image_url,
            }
            for asset in assets
        ],
        "code_batch": {
            "id": str(code_batch.id) if code_batch else None,
            "product_id": str(code_batch.product_id) if code_batch else None,
            "sku_id": str(code_batch.sku_id) if code_batch else None,
            "production_batch_id": str(code_batch.production_batch_id) if code_batch else None,
            "status": _manifest_value(code_batch.status) if code_batch else None,
        },
        "production_batch": {
            "id": str(production_batch.id) if production_batch else None,
            "product_id": str(production_batch.product_id) if production_batch else None,
            "sku_id": str(production_batch.sku_id) if production_batch else None,
            "batch_code": production_batch.batch_code if production_batch else None,
            "origin": production_batch.origin if production_batch else None,
            "status": _manifest_value(production_batch.status) if production_batch else None,
            "production_date": _manifest_value(production_batch.production_date) if production_batch else None,
            "expiry_date": _manifest_value(production_batch.expiry_date) if production_batch else None,
            "recall_reason": production_batch.recall_reason if production_batch else None,
            "recalled_at": _manifest_value(production_batch.recalled_at) if production_batch else None,
        },
        "sample_code": (
            {
                "id": str(sample_code.id),
                "public_id": sample_code.public_id,
                "status": _manifest_value(sample_code.status),
                "code_type": _manifest_value(sample_code.code_type),
                "code_batch_id": str(sample_code.code_batch_id),
            }
            if sample_code
            else None
        ),
        "takeover": [
            {
                "id": str(project.id),
                "mode": _manifest_value(project.mode),
                "status": _manifest_value(project.status),
                "configuration_version": project.configuration_version,
                "active_route_version_id": (
                    str(project.active_route_version_id) if project.active_route_version_id else None
                ),
            }
            for project in takeovers
        ],
        "ready": ready,
    }


def _as_aware(value: datetime) -> datetime:
    """SQLite 等测试驱动可能返回 naive datetime，按业务时区补齐 tzinfo。"""
    from zoneinfo import ZoneInfo

    return value if value.tzinfo is not None else value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


async def build_launch_readiness(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page_version_id: uuid.UUID,
    campaign_id: uuid.UUID,
    code_batch_id: uuid.UUID,
) -> tuple[dict, str, PageVersion | None, dict]:
    """重新计算上线事实，不读取 onboarding_progress，也不接受前端勾选结果。"""
    page_result = await db.execute(
        select(PageVersion, PageTemplate)
        .join(
            PageTemplate,
            (PageVersion.tenant_id == PageTemplate.tenant_id) & (PageVersion.page_template_id == PageTemplate.id),
        )
        .where(
            PageVersion.id == page_version_id,
            PageVersion.tenant_id == tenant_id,
            PageTemplate.tenant_id == tenant_id,
        )
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
        and page_template.status == "active"
        and code_batch
        and page_product_id
        and page_product_id == code_batch.product_id
    )
    # SQLite 等驱动可能返回 naive datetime（tzinfo 不落盘），比较前统一为 aware
    campaign_start = _as_aware(campaign.start_at) if campaign else None
    campaign_end = _as_aware(campaign.end_at) if campaign else None
    campaign_passed = bool(
        campaign
        and campaign.status == CampaignStatus.ACTIVE
        and campaign_start is not None
        and campaign_start <= datetime.now(UTC)
        and campaign_end is not None
        and campaign_end > datetime.now(UTC)
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

    sample_code = None
    if code_batch:
        sample_code = await db.scalar(
            select(CodeItem)
            .where(
                CodeItem.tenant_id == tenant_id,
                CodeItem.code_batch_id == code_batch.id,
                CodeItem.status.in_((CodeItemStatus.activated, CodeItemStatus.bound)),
            )
            .order_by(CodeItem.id)
            .limit(1)
        )
    sample_code_ready = sample_code is not None

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
            "sample_code_ready",
            "上线样本码已准备",
            sample_code_ready,
            f"系统已选定样本码 {sample_code.public_id}"
            if sample_code
            else "当前码批次没有可用于上线校验的已激活样本码",
        ),
    ]
    takeover_check = await build_takeover_launch_gate_check(db, tenant_id)
    if takeover_check:
        checks.append(takeover_check)
    snapshot = {
        "version": 3,
        "tenant_id": str(tenant_id),
        "page_version_id": str(page_version_id),
        "campaign_id": str(campaign_id),
        "code_batch_id": str(code_batch_id),
        "checks": checks,
        "passed_count": sum(1 for item in checks if item["passed"]),
        "total_count": len(checks),
        "ready": all(item["passed"] for item in checks),
        "sample_code": (
            {
                "id": str(sample_code.id),
                "public_id": sample_code.public_id,
                "status": sample_code.status.value if hasattr(sample_code.status, "value") else sample_code.status,
                "code_type": sample_code.code_type.value
                if hasattr(sample_code.code_type, "value")
                else sample_code.code_type,
                "code_batch_id": str(sample_code.code_batch_id),
            }
            if sample_code
            else None
        ),
        "calculated_at": datetime.now(UTC).isoformat(),
    }
    manifest = await _build_canonical_launch_manifest(
        db,
        tenant_id,
        page_version=page_version,
        page_template=page_template,
        campaign=campaign,
        code_batch=code_batch,
        production_batch=production_batch,
        sample_code=sample_code,
        ready=snapshot["ready"],
    )
    return snapshot, _digest(manifest), page_version, manifest


def serialize_launch_release(release: LaunchRelease) -> dict:
    snapshot = release.readiness_snapshot or {}
    manifest = release.readiness_manifest or {}
    sample = manifest.get("sample_code") if manifest.get("version") == 3 else None
    sample_status = sample.get("status") if isinstance(sample, dict) else None
    readiness_sample_code = None
    if isinstance(sample, dict) and isinstance(sample.get("public_id"), str):
        readiness_sample_code = {
            "public_id": sample["public_id"],
            "status": sample_status,
            "ready": sample_status in {CodeItemStatus.activated, CodeItemStatus.bound},
        }
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
        "readiness_sample_code": readiness_sample_code,
        "content_digest": release.content_digest,
        "brand_confirmed_by": release.brand_confirmed_by,
        "brand_confirmed_at": release.brand_confirmed_at,
        "launched_by": release.launched_by,
        "launched_at": release.launched_at,
        "failure_reason": release.failure_reason,
        "suspension_reason": release.suspension_reason,
    }


class LaunchScanObservation(TypedDict):
    release_id: uuid.UUID
    observation_status: str
    recorded_at: datetime
    replayed: bool


async def record_launch_release_valid_scan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    release_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
) -> LaunchScanObservation:
    """Attach a committed-in-this-transaction valid scan to the exact live release.

    PostgreSQL remains authoritative for validating the event, timestamp,
    tenant, code batch and live release.  The resolver calls this inside a
    savepoint so an observation conflict can be retried on a later valid scan
    without discarding the consumer's authoritative scan event.
    """
    if _session_uses_postgresql(db):
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM public.record_launch_release_valid_scan("
                        ":tenant_id,:release_id,:scan_event_id,:scan_time)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "release_id": release_id,
                        "scan_event_id": scan_event_id,
                        "scan_time": scan_time,
                    },
                )
            )
            .mappings()
            .one()
        )
        return LaunchScanObservation(
            release_id=uuid.UUID(str(row["release_id"])),
            observation_status=str(row["observation_status"]),
            recorded_at=row["recorded_at"],
            replayed=bool(row["replayed"]),
        )

    # SQLite is a test adapter only. Mirror the exact binding checks used by
    # the database function; do not accept any client-supplied validity fact.
    from app.models.scan import ScanEvent

    row = (
        await db.execute(
            select(LaunchRelease, ScanEvent)
            .join(
                CodeItem,
                (CodeItem.tenant_id == LaunchRelease.tenant_id)
                & (CodeItem.code_batch_id == LaunchRelease.code_batch_id),
            )
            .join(
                ScanEvent,
                (ScanEvent.tenant_id == CodeItem.tenant_id) & (ScanEvent.public_id == CodeItem.public_id),
            )
            .where(
                LaunchRelease.tenant_id == tenant_id,
                LaunchRelease.id == release_id,
                LaunchRelease.status == LaunchReleaseStatus.live,
                LaunchRelease.launched_at.is_not(None),
                ScanEvent.id == scan_event_id,
                ScanEvent.scan_time == scan_time,
                ScanEvent.scan_time >= LaunchRelease.launched_at,
                ScanEvent.is_valid_visit.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise RuntimeError("launch scan observation rejected")
    release, _event = row
    if release.first_valid_scan_event_id is None:
        release.first_valid_scan_event_id = scan_event_id
        release.first_valid_scan_time = scan_time
        await db.flush()
        return LaunchScanObservation(
            release_id=release.id,
            observation_status="observed",
            recorded_at=scan_time,
            replayed=False,
        )
    return LaunchScanObservation(
        release_id=release.id,
        observation_status="already_observed",
        recorded_at=release.first_valid_scan_time,
        replayed=True,
    )


async def get_launch_release(db: AsyncSession, tenant_id: uuid.UUID, release_id: uuid.UUID) -> LaunchRelease | None:
    return await db.scalar(
        select(LaunchRelease).where(LaunchRelease.id == release_id, LaunchRelease.tenant_id == tenant_id)
    )


async def _reload_launch_release(db: AsyncSession, tenant_id: uuid.UUID, release_id: uuid.UUID) -> LaunchRelease | None:
    """Overwrite any stale identity-map state after a DB authority function mutates the row."""
    return await db.scalar(
        select(LaunchRelease)
        .where(LaunchRelease.id == release_id, LaunchRelease.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )


async def resolve_current_launch_release(db: AsyncSession, tenant_id: uuid.UUID, public_id: str) -> dict | None:
    """Return the one DB-authoritative live release for a public code.

    No row is the intentional fail-closed paused result. The public resolver
    must never choose a page or campaign independently of this result.
    """
    if _session_uses_postgresql(db):
        try:
            row = (
                (
                    await db.execute(
                        text("SELECT * FROM public.resolve_current_launch_release(:tenant_id,:public_id)"),
                        {"tenant_id": tenant_id, "public_id": public_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        except DBAPIError as exc:
            mapped = _map_launch_db_error(exc)
            if mapped is None:
                raise
            raise mapped from exc
        return dict(row) if row else None

    row = (
        await db.execute(
            select(LaunchRelease, PageVersion.page_template_id)
            .join(
                CodeItem,
                (CodeItem.tenant_id == LaunchRelease.tenant_id)
                & (CodeItem.code_batch_id == LaunchRelease.code_batch_id),
            )
            .join(
                PageVersion,
                (PageVersion.tenant_id == LaunchRelease.tenant_id) & (PageVersion.id == LaunchRelease.page_version_id),
            )
            .where(
                LaunchRelease.tenant_id == tenant_id,
                LaunchRelease.status == LaunchReleaseStatus.live,
                CodeItem.public_id == public_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    release, page_template_id = row
    await refresh_launch_release(db, release)
    if release.status != LaunchReleaseStatus.live:
        return None
    return {
        "release_id": release.id,
        "page_template_id": page_template_id,
        "page_version_id": release.page_version_id,
        "campaign_id": release.campaign_id,
        "code_batch_id": release.code_batch_id,
        "content_digest": release.content_digest,
    }


async def refresh_launch_release(db: AsyncSession, release: LaunchRelease) -> LaunchRelease:
    snapshot, digest, _, manifest = await build_launch_readiness(
        db,
        release.tenant_id,
        page_version_id=release.page_version_id,
        campaign_id=release.campaign_id,
        code_batch_id=release.code_batch_id,
    )
    release.readiness_snapshot = snapshot
    release.readiness_manifest = manifest
    sample = snapshot.get("sample_code")
    release.readiness_code_item_id = uuid.UUID(sample["id"]) if sample else None
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
    idempotency_key: str | None = None,
) -> LaunchRelease:
    if _session_uses_postgresql(db):
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="Launch idempotency key is required")
        release_id = uuid7()
        result = await _launch_authority_one(
            db,
            "SELECT * FROM public.create_launch_release(:tenant_id,:auth_session_id,:audit_id,:action_id,"
            ":release_id,:page_version_id,:campaign_id,:code_batch_id,:idempotency_key)",
            _authority_actor_params(tenant_id, "create")
            | {
                "release_id": release_id,
                "page_version_id": page_version_id,
                "campaign_id": campaign_id,
                "code_batch_id": code_batch_id,
                "idempotency_key": idempotency_key,
            },
        )
        # Exact create replays intentionally return the first durable release,
        # not the fresh caller-proposed UUID.
        release = await _reload_launch_release(db, tenant_id, uuid.UUID(str(result["release_id"])))
        if release is None:
            raise RuntimeError("launch authority returned no durable release")
        return release
    snapshot, digest, page_version, manifest = await build_launch_readiness(
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
        readiness_manifest=manifest,
        readiness_code_item_id=uuid.UUID(snapshot["sample_code"]["id"]) if snapshot.get("sample_code") else None,
        content_digest=digest,
        created_by=account_id,
        created_by_tenant_id=tenant_id,
        idempotency_key=idempotency_key,
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


async def confirm_launch_release(
    db: AsyncSession,
    release: LaunchRelease,
    account_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> LaunchRelease:
    if _session_uses_postgresql(db):
        await _launch_authority_one(
            db,
            "SELECT * FROM public.confirm_launch_release(:tenant_id,:auth_session_id,:audit_id,:action_id,"
            ":release_id,:expected_digest,:idempotency_key)",
            _authority_actor_params(release.tenant_id, "confirm")
            | {
                "release_id": release.id,
                "expected_digest": release.content_digest,
                "idempotency_key": idempotency_key,
            },
        )
        durable = await _reload_launch_release(db, release.tenant_id, release.id)
        if durable is None:
            raise RuntimeError("launch authority returned no durable release")
        return durable
    await refresh_launch_release(db, release)
    if not release.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线准备度未通过，暂时不能确认")
    release.status = LaunchReleaseStatus.confirmed
    release.brand_confirmed_by = account_id
    release.brand_confirmed_by_tenant_id = release.tenant_id
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
    del db, release, account_id, idempotency_key
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先确认版本，再执行上线")


async def launch_confirmed_release(
    db: AsyncSession, release: LaunchRelease, account_id: uuid.UUID, idempotency_key: str
) -> LaunchRelease:
    """代运营在已完成品牌确认后执行发布；不会替品牌方确认。"""
    if _session_uses_postgresql(db):
        await _launch_authority_one(
            db,
            "SELECT * FROM public.launch_launch_release(:tenant_id,:auth_session_id,:audit_id,:action_id,"
            ":release_id,:expected_digest,:idempotency_key)",
            _authority_actor_params(release.tenant_id, "launch")
            | {
                "release_id": release.id,
                "expected_digest": release.content_digest,
                "idempotency_key": idempotency_key,
            },
        )
        durable = await _reload_launch_release(db, release.tenant_id, release.id)
        if durable is None:
            raise RuntimeError("launch authority returned no durable release")
        return durable
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
    release.launched_by_tenant_id = release.tenant_id
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


async def suspend_launch_release(
    db: AsyncSession,
    release: LaunchRelease,
    account_id: uuid.UUID,
    reason: str,
    idempotency_key: str | None = None,
) -> LaunchRelease:
    if _session_uses_postgresql(db):
        await _launch_authority_one(
            db,
            "SELECT * FROM public.suspend_launch_release(:tenant_id,:auth_session_id,:audit_id,:action_id,"
            ":release_id,:reason,:idempotency_key)",
            _authority_actor_params(release.tenant_id, "suspend")
            | {"release_id": release.id, "reason": reason, "idempotency_key": idempotency_key},
        )
        durable = await _reload_launch_release(db, release.tenant_id, release.id)
        if durable is None:
            raise RuntimeError("launch authority returned no durable release")
        return durable
    if release.status != LaunchReleaseStatus.live:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有已正式上线版本可以暂停")
    release.status = LaunchReleaseStatus.suspended
    release.suspended_by = account_id
    release.suspended_by_tenant_id = release.tenant_id
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


async def resume_launch_release(
    db: AsyncSession,
    release: LaunchRelease,
    account_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> LaunchRelease:
    if _session_uses_postgresql(db):
        await _launch_authority_one(
            db,
            "SELECT * FROM public.resume_launch_release(:tenant_id,:auth_session_id,:audit_id,:action_id,"
            ":release_id,:expected_digest,:idempotency_key)",
            _authority_actor_params(release.tenant_id, "resume")
            | {
                "release_id": release.id,
                "expected_digest": release.content_digest,
                "idempotency_key": idempotency_key,
            },
        )
        durable = await _reload_launch_release(db, release.tenant_id, release.id)
        if durable is None:
            raise RuntimeError("launch authority returned no durable release")
        return durable
    if release.status != LaunchReleaseStatus.suspended:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有已暂停版本可以恢复")
    await refresh_launch_release(db, release)
    if not release.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线准备度已变化，需要重新确认")
    if release.brand_confirmation_digest != release.content_digest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线版本已变化，需要重新确认")
    release.status = LaunchReleaseStatus.live
    release.suspended_by = None
    release.suspended_by_tenant_id = None
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
