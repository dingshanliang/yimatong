"""只读 PG 证据验证器 — yimatong-zgb1.1。

每个验证函数返回 BASELINE_ACCEPTANCE_MATRIX.md §12 契约结构：
    {
      "scenario": str,
      "dataset_version": str,
      "status": "passed" | "failed",
      "gate": str,                # 所属门禁
      "release_blocking": bool,
      "tenant": str | None,
      "stable_identifiers": dict,
      "db_assertions": dict,      # 业务可读的数据库断言结果
      "failure_reason": str | None,
      "executed_at": str,         # ISO 时间
    }

只允许 SELECT。任何写操作都是验证器的 bug。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.baseline import (
    BASELINE_TENANT_SLUG,
    BRAND_NAME,
    CAMPAIGN_NAME,
    CODE_BATCH_CODE,
    CONTROL_TENANT_SLUG,
    PRODUCT_NAME,
    PRODUCTION_BATCH_CODE,
    SKU_CODE,
)
from app.models.campaign import Benefit, Campaign
from app.models.code import CodeBatch, CodeItem
from app.models.launch import LaunchRelease, LaunchReleaseStatus
from app.models.page import PageTemplate, PageVersion, PageVersionStatus
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.tenant import Tenant

DATASET_VERSION = "yimatong-member-repurchase-v1"


def _evidence(
    scenario: str,
    gate: str,
    *,
    passed: bool,
    tenant: str | None,
    stable_identifiers: dict[str, Any],
    db_assertions: dict[str, Any],
    failure_reason: str | None = None,
    release_blocking: bool = True,
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "dataset_version": DATASET_VERSION,
        "status": "passed" if passed else "failed",
        "gate": gate,
        "release_blocking": release_blocking,
        "tenant": tenant,
        "stable_identifiers": stable_identifiers,
        "db_assertions": db_assertions,
        "failure_reason": failure_reason,
        "executed_at": datetime.now(UTC).isoformat(),
    }


async def _tenant_id(db: AsyncSession, slug: str) -> str | None:
    result = await db.execute(select(Tenant.id).where(Tenant.slug == slug))
    row = result.first()
    return str(row[0]) if row else None


async def verify_baseline_presence(db: AsyncSession) -> dict[str, Any]:
    """核对基准租户与对照租户的全部稳定标识对应记录都存在。"""
    base_id = await _tenant_id(db, BASELINE_TENANT_SLUG)
    ctrl_id = await _tenant_id(db, CONTROL_TENANT_SLUG)

    assertions: dict[str, Any] = {}
    failures: list[str] = []

    if base_id:
        assertions["baseline_tenant"] = {"id": base_id, "slug": BASELINE_TENANT_SLUG}
    else:
        failures.append("baseline tenant missing")
        assertions["baseline_tenant"] = None

    if ctrl_id:
        assertions["control_tenant"] = {"id": ctrl_id, "slug": CONTROL_TENANT_SLUG}
    else:
        failures.append("control tenant missing")
        assertions["control_tenant"] = None

    if base_id:
        # 各业务实体计数（用稳定标识定位）
        checks = [
            ("brand", Brand, Brand.name == BRAND_NAME, 1),
            ("product", Product, Product.name == PRODUCT_NAME, 1),
            ("sku", SKU, SKU.code == SKU_CODE, 1),
            ("production_batch", ProductionBatch, ProductionBatch.batch_code == PRODUCTION_BATCH_CODE, 1),
            ("code_batch", CodeBatch, CodeBatch.batch_code == CODE_BATCH_CODE, 1),
            ("page_template", PageTemplate, PageTemplate.name == "PAGE-BASE-001", 1),
            ("campaign", Campaign, Campaign.name == CAMPAIGN_NAME, 1),
            ("benefit", Benefit, Benefit.name == "BENEFIT-BASE-001", 1),
        ]
        for label, model, cond, expected in checks:
            result = await db.execute(select(func.count()).select_from(model).where(model.tenant_id == base_id, cond))
            count = int(result.scalar_one())
            assertions[label] = {"count": count, "expected": expected}
            if count != expected:
                failures.append(f"{label} expected {expected} got {count}")

        # 激活码数量 = BASELINE_CODE_QUANTITY（20）
        result = await db.execute(select(func.count()).select_from(CodeItem).where(CodeItem.tenant_id == base_id))
        code_count = int(result.scalar_one())
        assertions["code_items"] = {"count": code_count, "expected": 20}
        if code_count != 20:
            failures.append(f"code_items expected 20 got {code_count}")

        # 已发布页面版本存在
        result = await db.execute(
            select(func.count())
            .select_from(PageVersion)
            .where(
                PageVersion.tenant_id == base_id,
                PageVersion.status == PageVersionStatus.published,
            )
        )
        published = int(result.scalar_one())
        assertions["published_page_version"] = {"count": published, "expected_gte": 1}
        if published < 1:
            failures.append("no published page version")

        result = await db.execute(
            select(func.count())
            .select_from(LaunchRelease)
            .where(
                LaunchRelease.tenant_id == base_id,
                LaunchRelease.status == LaunchReleaseStatus.live,
            )
        )
        live_releases = int(result.scalar_one())
        assertions["live_launch_release"] = {"count": live_releases, "expected": 1}
        if live_releases != 1:
            failures.append(f"live launch release expected 1 got {live_releases}")

    return _evidence(
        scenario="baseline_presence",
        gate="clean_env_rebuild",
        passed=not failures,
        tenant=base_id,
        stable_identifiers={
            "tenant_slug": BASELINE_TENANT_SLUG,
            "control_slug": CONTROL_TENANT_SLUG,
            "brand": BRAND_NAME,
            "product": PRODUCT_NAME,
            "sku": SKU_CODE,
            "production_batch": PRODUCTION_BATCH_CODE,
            "code_batch": CODE_BATCH_CODE,
        },
        db_assertions=assertions,
        failure_reason="; ".join(failures) if failures else None,
    )


async def verify_isolation_rls(
    db_with_bypass: AsyncSession, base_slug: str = BASELINE_TENANT_SLUG, control_slug: str = CONTROL_TENANT_SLUG
) -> dict[str, Any]:
    """真实 PG RLS 隔离证据。

    使用 bypass 会话拿到两个租户的 id 与基准租户的业务行数，然后调用方（测试）用
    asyncpg 在 `SET LOCAL app.tenant_id=control` 上下文里查询，应当看不到基准租户任何行。
    本函数只负责收集“应当不可见”的预期值；真正的 RLS 断言在 asyncpg 直连里完成。
    """
    base_id = await _tenant_id(db_with_bypass, base_slug)
    ctrl_id = await _tenant_id(db_with_bypass, control_slug)

    assertions: dict[str, Any] = {
        "base_tenant_id": base_id,
        "control_tenant_id": ctrl_id,
    }
    if base_id:
        for label, model, cond in [
            ("brands", Brand, Brand.name == BRAND_NAME),
            ("products", Product, Product.name == PRODUCT_NAME),
            ("code_items", CodeItem, CodeItem.tenant_id == base_id),  # type: ignore[arg-type]
        ]:
            result = await db_with_bypass.execute(
                select(func.count()).select_from(model).where(model.tenant_id == base_id, cond)
            )
            assertions[f"baseline_{label}_count"] = int(result.scalar_one())

    return _evidence(
        scenario="rls_isolation",
        gate="multi_tenant_isolation",
        passed=base_id is not None and ctrl_id is not None,
        tenant=base_id,
        stable_identifiers={"base_slug": base_slug, "control_slug": control_slug},
        db_assertions=assertions,
        failure_reason=None if (base_id and ctrl_id) else "tenant(s) missing",
    )


async def verify_no_duplicate_on_rerun(db: AsyncSession) -> dict[str, Any]:
    """连续两次重建后，关键唯一字段不产生重复。"""
    base_id = await _tenant_id(db, BASELINE_TENANT_SLUG)
    assertions: dict[str, Any] = {}
    failures: list[str] = []
    if not base_id:
        return _evidence(
            scenario="idempotent_rerun",
            gate="clean_env_rebuild",
            passed=False,
            tenant=None,
            stable_identifiers={},
            db_assertions={},
            failure_reason="baseline tenant missing",
        )

    # 每个 (tenant, batch_code) / (tenant, name) 应唯一
    dup_checks = [
        ("code_batches", CodeBatch, CodeBatch.batch_code, CODE_BATCH_CODE),
        ("production_batches", ProductionBatch, ProductionBatch.batch_code, PRODUCTION_BATCH_CODE),
        ("brands", Brand, Brand.name, BRAND_NAME),
        ("products", Product, Product.name, PRODUCT_NAME),
        ("skus", SKU, SKU.code, SKU_CODE),
    ]
    for label, model, field, value in dup_checks:
        result = await db.execute(
            select(func.count()).select_from(model).where(model.tenant_id == base_id, field == value)
        )
        count = int(result.scalar_one())
        assertions[label] = {"count": count, "expected": 1}
        if count != 1:
            failures.append(f"{label} duplicate: count={count}")

    # 同 public_id 全局唯一（跨租户也不重复）
    result = await db.execute(
        text("SELECT public_id, count(*) c FROM code_items GROUP BY public_id HAVING count(*) > 1 LIMIT 5")
    )
    dups = result.fetchall()
    assertions["duplicate_public_ids"] = [r[0] for r in dups]
    if dups:
        failures.append(f"duplicate public_ids: {assertions['duplicate_public_ids']}")

    return _evidence(
        scenario="idempotent_rerun",
        gate="clean_env_rebuild",
        passed=not failures,
        tenant=base_id,
        stable_identifiers={"tenant_slug": BASELINE_TENANT_SLUG},
        db_assertions=assertions,
        failure_reason="; ".join(failures) if failures else None,
    )


async def verify_first_scan_atomicity(db: AsyncSession, public_id: str) -> dict[str, Any]:
    """首扫原子性证据：同一码的 is_first_scan=true 事件恰好 1 条。

    首扫原子性由 backend/app/services/scan_event.py:26-33 的
    `UPDATE code_items SET first_scanned_at=now() WHERE public_id=:pid AND first_scanned_at IS NULL`
    保证。本函数证明该不变式在持久化层成立。
    """
    from app.models.scan import ScanEvent

    result = await db.execute(
        select(func.count())
        .select_from(ScanEvent)
        .where(ScanEvent.public_id == public_id, ScanEvent.is_first_scan.is_(True))
    )
    first_count = int(result.scalar_one())
    result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id))
    total = int(result.scalar_one())

    passed = first_count == 1
    return _evidence(
        scenario="first_scan_atomic",
        gate="traceability_light_verification",
        passed=passed,
        tenant=None,
        stable_identifiers={"public_id": public_id},
        db_assertions={
            "first_scan_events": first_count,
            "expected_first": 1,
            "total_scan_events": total,
        },
        failure_reason=None if passed else f"expected exactly 1 first-scan event, got {first_count}",
    )


async def verify_duplicate_scan_recording(
    db: AsyncSession, public_id: str, *, expected_min_total: int = 2
) -> dict[str, Any]:
    """重复扫码记录现状证据（yimatong-zgb1.1 仅暴露，不修复）。

    当前 resolver 对激活码每次请求都新增一行 scan_events（无访客/IP/设备去重）。
    本证据记录该观测事实，修复留给后续票。
    """
    from app.models.scan import ScanEvent

    result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id))
    total = int(result.scalar_one())
    # 这是现状证据：passed=True 表示“观测事实被正确记录”，不代表行为正确。
    observed = total >= expected_min_total
    return _evidence(
        scenario="duplicate_scan_recording_observed",
        gate="traceability_light_verification",
        passed=observed,
        tenant=None,
        stable_identifiers={"public_id": public_id},
        db_assertions={
            "total_scan_events": total,
            "expected_min_total": expected_min_total,
            "note": "current behavior: every activated-code resolve inserts a new ScanEvent; "
            "dedup is out of scope for yimatong-zgb1.1 (tracked for a later ticket)",
        },
        failure_reason=None
        if observed
        else f"expected >={expected_min_total} scan rows to expose dup behavior, got {total}",
        # 这是一条“现状证据”，不阻断本票发布（行为本身是已知问题，由后续票修复）
        release_blocking=False,
    )
