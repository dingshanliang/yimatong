"""基准数据 CLI — 从干净环境可重复创建的基准租户与隔离对照租户。

本模块服务于 yimatong-zgb1.1：建立统一产品验收的基准底座。与 `app.cli.seed` 的演示
数据严格分离，使用稳定业务标识（TENANT-BASE / BRAND-BASE / ...），保证连续运行两次
不产生重复记录或状态漂移。

权威资料：docs/01_product/BASELINE_ACCEPTANCE_MATRIX.md §2（基准数据结构）。
本模块只实现可重复创建，不复制规格文案。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import typer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.constants.categories import get_default_categories
from app.core.config import settings
from app.core.database import set_session_tenant_context
from app.models.campaign import Benefit, Campaign
from app.models.code import CodeBatch, CodeItem, CodeItemStatus
from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus, TemplateType
from app.models.plan import PlanDefinition
from app.models.product import (
    SKU,
    Brand,
    Product,
    ProductAsset,
    ProductAssetStatus,
    ProductAssetType,
    ProductionBatch,
)
from app.models.tenant import (
    Account,
    Organization,
    Role,
    Tenant,
    TenantPlan,
    TenantStatus,
    TenantType,
    account_roles,
)
from app.services.audit import write_audit_log
from app.services.auth import revoke_current_tenant_account_sessions
from app.services.code import activate_batch, create_code_batch
from app.services.entitlement import TenantPlanExpiredError, require_active_plan, validate_feature_flags
from app.services.quota import (
    lock_quota_rollout_state,
    refresh_quota_usage_from_authoritative_rows,
    validate_quota_config,
)
from app.utils import utcnow
from app.utils.security import hash_password, verify_password

# ── 稳定业务标识 ──────────────────────────────────────────────────────────
# 对应 BASELINE_ACCEPTANCE_MATRIX.md §2 的“稳定业务标识示例”。这些不是数据库主键，
# 而是用于定位场景与复现失败的人类可读标识，写入 external_id/name/batch_code 等字段。

BASELINE_TENANT_SLUG = "baseline-base"
CONTROL_TENANT_SLUG = "baseline-control"

BASELINE_ADMIN_EMAIL = "admin@baseline.local"
BASELINE_ADMIN_PASSWORD = "Baseline1234"
CONTROL_ADMIN_EMAIL = "admin@baseline-control.local"
CONTROL_ADMIN_PASSWORD = "Control1234"

BRAND_NAME = "BRAND-BASE"
PRODUCT_NAME = "PRODUCT-BASE"
SKU_CODE = "SKU-BASE-001"
PRODUCTION_BATCH_CODE = "PB-BASE-001"
CODE_BATCH_CODE = "CB-BASE-001"
PAGE_TEMPLATE_NAME = "PAGE-BASE-001"
CAMPAIGN_NAME = "CAMPAIGN-BASE-001"
BENEFIT_NAME = "BENEFIT-BASE-001"
REPORT_NAME = "REPORT-BASE-001"
CERTIFICATE_NAME = "CERT-BASE-001"

# 对照租户使用相同名称，专门用于证明同名碰撞不能突破隔离。
CONTROL_BRAND_NAME = BRAND_NAME
CONTROL_PRODUCT_NAME = PRODUCT_NAME
CONTROL_SKU_CODE = SKU_CODE
CONTROL_CODE_BATCH_CODE = "CB-CTRL-001"

BASELINE_CODE_QUANTITY = 20
CONTROL_CODE_QUANTITY = 3

app = typer.Typer(help="Baseline dataset for product acceptance (yimatong-zgb1.1)")


def _guard_baseline_build(*, target: str, allow_production: bool) -> None:
    """Reject an unapproved environment or target before opening an engine."""

    if settings.environment == "production":
        authority_note = "；--allow-production 不适用于该命令" if allow_production else ""
        typer.echo(f"production 环境禁止构建包含仓库内置账号密码的基准数据{authority_note}", err=True)
        raise typer.Exit(code=2)
    if target != BASELINE_TENANT_SLUG:
        typer.echo(
            f"拒绝写入非 {BASELINE_TENANT_SLUG} 目标；请显式传 --target {BASELINE_TENANT_SLUG}",
            err=True,
        )
        raise typer.Exit(code=2)


class BaselineRecoveryRequired(RuntimeError):
    """A committed earlier stage is safe to retain and an idempotent rerun is required."""


@dataclass(frozen=True, slots=True)
class _TenantSeedRef:
    tenant_id: uuid.UUID
    tenant_slug: str
    admin_email: str
    admin_name: str
    admin_password: str


@dataclass(frozen=True, slots=True)
class _BaselineFacts:
    brand_id: uuid.UUID
    product_id: uuid.UUID
    sku_id: uuid.UUID
    production_batch_id: uuid.UUID
    code_batch_id: uuid.UUID
    code_quantity: int
    first_public_id: str | None
    page_template_id: uuid.UUID
    page_version_id: uuid.UUID
    page_version_status: str
    campaign_id: uuid.UUID
    benefit_id: uuid.UUID
    report_id: uuid.UUID
    certificate_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _ControlFacts:
    brand_id: uuid.UUID
    first_public_id: str | None


def _resolve_runtime_database_url(override: str | None = None) -> str:
    """Resolve the tenant-runtime connection URL."""

    return override or str(settings.database_url)


def _resolve_control_database_url(
    runtime_override: str | None = None,
    control_override: str | None = None,
) -> str:
    """Resolve the privileged control URL without granting runtime bypass.

    A legacy one-URL override remains self-contained for existing acceptance
    callers. The CLI path uses the configured control/migration URL and never
    substitutes the restricted runtime URL when an explicit control URL exists.
    """

    if control_override is not None:
        return control_override
    if runtime_override is not None:
        return runtime_override
    return str(settings.control_database_url or settings.migration_database_url or settings.database_url)


def _session_factory(url: str) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(url)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _prepare_control_session(db: AsyncSession) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))


# ── 平台级 identity bootstrap（业务修复必须留在 tenant runtime transaction）──


async def _get_or_create_tenant_identity(
    db: AsyncSession,
    slug: str,
    name: str,
    industry: str | None = None,
) -> Tenant:
    """Discover an existing identity or create only the Tenant control row.

    Existing tenants are strictly read-only in this stage. All tenant-owned
    account, organization, RBAC, credential, usage, and business repair is
    deferred until the runtime transaction has locked and validated the plan.
    """
    result = await db.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant

    # Tenant birth must participate in the rollout activation mutex. Lock the
    # global epoch before the slug namespace, then recheck after both locks.
    await lock_quota_rollout_state(db)
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_key, 0))"),
            {"tenant_key": f"baseline-identity:{slug}"},
        )
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is not None:
        return tenant

    plan = await db.scalar(
        select(PlanDefinition).where(
            PlanDefinition.name == TenantPlan.free.value,
            PlanDefinition.is_active.is_(True),
        )
    )
    if plan is None:
        raise BaselineRecoveryRequired("Baseline tenant identity requires an active free plan definition")
    try:
        quota = validate_quota_config(plan.quota_defaults)
        features = validate_feature_flags(plan.feature_flags)
    except ValueError as exc:
        raise BaselineRecoveryRequired("Baseline tenant identity cannot use the configured free plan") from exc

    tenant = Tenant(
        name=name,
        slug=slug,
        status=TenantStatus.active,
        plan=TenantPlan.free,
        tenant_type=TenantType.brand,
        industry=industry,
        quota=quota,
        enabled_features=features,
        categories=list(get_default_categories(industry)),
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def _ensure_runtime_admin(
    db: AsyncSession,
    tenant: Tenant,
    tenant_ref: _TenantSeedRef,
) -> Account:
    """Repair the baseline admin graph after the active-plan gate."""

    default_organization_name = f"{tenant.name} 默认组织"
    result = await db.execute(
        select(Organization).where(
            Organization.tenant_id == tenant.id,
            Organization.name == default_organization_name,
        )
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(tenant_id=tenant.id, name=default_organization_name)
        db.add(org)
        await db.flush()

    # 确保账号存在
    result = await db.execute(
        select(Account)
        .options(selectinload(Account.roles))
        .where(Account.tenant_id == tenant.id, Account.email == tenant_ref.admin_email)
    )
    account = result.scalar_one_or_none()
    account_created = account is None
    if account_created:
        account = Account(
            tenant_id=tenant.id,
            organization_id=org.id,
            email=tenant_ref.admin_email,
            hashed_password=hash_password(tenant_ref.admin_password),
            name=tenant_ref.admin_name,
        )
        db.add(account)
        await db.flush()
    # 确保 admin 角色存在并关联
    result = await db.execute(select(Role).where(Role.tenant_id == tenant.id, Role.name == "admin"))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(tenant_id=tenant.id, name="admin", description="品牌管理员")
        db.add(role)
        await db.flush()

    if account_created:
        current_role_ids = set()
    else:
        current_role_ids = {current_role.id for current_role in account.roles}
    if account_created:
        await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=role.id))
    elif account.id is not None:
        identity_changes: list[str] = []
        security_changes: list[str] = []
        current_organization = await db.scalar(
            select(Organization).where(
                Organization.tenant_id == tenant.id,
                Organization.id == account.organization_id,
            )
        )
        before = {
            "name": account.name,
            "organization": {
                "id": str(account.organization_id),
                "name": current_organization.name if current_organization else None,
            },
            "roles": [
                {"id": str(current_role.id), "name": current_role.name}
                for current_role in sorted(
                    account.roles, key=lambda current_role: (current_role.name, str(current_role.id))
                )
            ],
        }
        if account.name != tenant_ref.admin_name:
            account.name = tenant_ref.admin_name
            identity_changes.append("name")
        if account.organization_id != org.id:
            account.organization_id = org.id
            security_changes.append("organization")
        if not verify_password(tenant_ref.admin_password, account.hashed_password):
            account.hashed_password = hash_password(tenant_ref.admin_password)
            security_changes.append("password")
        if current_role_ids != {role.id}:
            await db.execute(
                account_roles.delete().where(
                    account_roles.c.tenant_id == tenant.id,
                    account_roles.c.account_id == account.id,
                )
            )
            await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=role.id))
            security_changes.append("roles")
        identity_changes.extend(security_changes)
        if security_changes:
            account.auth_version += 1
            await revoke_current_tenant_account_sessions(db, account.id)
        if identity_changes:
            await write_audit_log(
                db,
                operator_id="baseline-cli",
                target_tenant_id=str(tenant.id),
                action="account_identity_reconciled",
                resource=f"account:{account.id}",
                details={
                    "resource_name": account.name,
                    "changed_fields": sorted(identity_changes),
                    "before": before,
                    "after": {
                        "name": account.name,
                        "organization": {"id": str(org.id), "name": org.name},
                        "roles": [{"id": str(role.id), "name": role.name}],
                    },
                    "result": "success",
                },
            )

    await db.flush()
    await db.refresh(account)
    return account


# ── 租户作用域的幂等业务实体创建（模拟品牌管理员经 API 的真实路径）───────────


async def _ensure_brand(db: AsyncSession, tenant_id: uuid.UUID, name: str, description: str) -> Brand:
    result = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name))
    brand = result.scalar_one_or_none()
    if brand is None:
        brand = Brand(tenant_id=tenant_id, name=name, description=description)
        db.add(brand)
        await db.flush()
        await db.refresh(brand)
    return brand


async def _ensure_product(
    db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID, name: str, *, category: str, origin: str
) -> Product:
    result = await db.execute(select(Product).where(Product.tenant_id == tenant_id, Product.name == name))
    product = result.scalar_one_or_none()
    if product is None:
        product = Product(
            tenant_id=tenant_id,
            brand_id=brand_id,
            name=name,
            category=category,
            origin=origin,
        )
        db.add(product)
        await db.flush()
        await db.refresh(product)
    return product


async def _ensure_sku(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID, code: str, name: str) -> SKU:
    result = await db.execute(
        select(SKU).where(SKU.tenant_id == tenant_id, SKU.product_id == product_id, SKU.code == code)
    )
    sku = result.scalar_one_or_none()
    if sku is None:
        sku = SKU(tenant_id=tenant_id, product_id=product_id, code=code, name=name)
        db.add(sku)
        await db.flush()
        await db.refresh(sku)
    return sku


async def _ensure_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    batch_code: str,
    external_id: str,
) -> ProductionBatch:
    result = await db.execute(
        select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.batch_code == batch_code)
    )
    pb = result.scalar_one_or_none()
    if pb is None:
        today = date.today()
        pb = ProductionBatch(
            tenant_id=tenant_id,
            product_id=product_id,
            sku_id=sku_id,
            batch_code=batch_code,
            production_date=today - timedelta(days=10),
            expiry_date=today + timedelta(days=365),
            origin="基准产地",
            external_id=external_id,
            source_system="baseline",
        )
        db.add(pb)
        await db.flush()
        await db.refresh(pb)
    return pb


async def _ensure_code_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    production_batch_id: uuid.UUID,
    batch_code: str,
    quantity: int,
    created_by: uuid.UUID,
) -> tuple[CodeBatch, list[CodeItem]]:
    """幂等创建并激活码批次。返回 (batch, items)。

    `create_code_batch` service 用 `production_batch.batch_code` 作为 batch_code
    （见 backend/app/services/code.py:82），不接受自定义 batch_code。因此：
    1. 先按稳定 batch_code 查；
    2. 若未命中，再按 production_batch_id 查（兼容历史/demo 数据衍生的 batch_code）；
    3. 若都没有，调 service 创建，随后在会话内将 batch_code 校正为稳定标识。
    这保持在受支持的创建路径内，仅做确定性标识归一化。
    """
    result = await db.execute(
        select(CodeBatch).where(CodeBatch.tenant_id == tenant_id, CodeBatch.batch_code == batch_code)
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        result = await db.execute(
            select(CodeBatch).where(
                CodeBatch.tenant_id == tenant_id,
                CodeBatch.production_batch_id == production_batch_id,
            )
        )
        batch = result.scalar_one_or_none()
    if batch is None:
        data = await create_code_batch(
            db,
            tenant_id,
            product_id,
            sku_id,
            production_batch_id,
            quantity,
            created_by,
        )
        batch_id = uuid.UUID(data["id"])
        await activate_batch(db, tenant_id, batch_id)
        result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))
        batch = result.scalar_one()

    # 将 batch_code 归一化为稳定标识（service 创建时会用 production_batch.batch_code）
    if batch.batch_code != batch_code:
        batch.batch_code = batch_code
        await db.flush()

    result = await db.execute(
        select(CodeItem).where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id)
    )
    items = list(result.scalars().all())
    # 保证已激活（二次运行若状态漂移可纠正）
    for item in items:
        if item.status == CodeItemStatus.created:
            item.status = CodeItemStatus.activated
            item.activated_at = utcnow()
    await db.flush()
    return batch, items


async def _ensure_page(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    name: str,
    created_by: uuid.UUID,
    benefit_name_hint: str | None = None,
) -> tuple[PageTemplate, PageVersion]:
    """幂等创建页面模板 + 已发布版本。页面通过模块引用权威产品/批次字段，不复制其值。"""
    result = await db.execute(
        select(PageTemplate).where(PageTemplate.tenant_id == tenant_id, PageTemplate.name == name)
    )
    template = result.scalar_one_or_none()
    if template is None:
        template = PageTemplate(
            tenant_id=tenant_id,
            product_id=product_id,
            name=name,
            template_type=TemplateType.traceability,
            status=PageTemplateStatus.active,
            description="基准验收页面：引用权威产品与生产批次模块。",
        )
        db.add(template)
        await db.flush()
        await db.refresh(template)

    result = await db.execute(
        select(PageVersion).where(
            PageVersion.tenant_id == tenant_id,
            PageVersion.page_template_id == template.id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    version = result.scalar_one_or_none()

    # 页面模块选择“展示哪些权威字段”，不拥有产品/批次事实的副本。
    config_json: dict[str, Any] = {
        "modules": [
            {"id": "hero", "type": "product_hero", "enabled": True, "config": {"show_verify_badge": True}},
            {
                "id": "trace",
                "type": "light_traceability",
                "enabled": True,
                "config": {"fields": ["origin", "production_date", "expiry_date"]},
            },
            {
                "id": "benefit",
                "type": "benefit_card",
                "enabled": True,
                "config": {
                    "benefit_id": "",
                    "benefit_type": "platform_coupon",
                    "title": benefit_name_hint or "基准复购权益",
                    "description": "基准验收用可幂等领取的权益",
                },
            },
        ],
        "routing": {"default_page": True, "campaign_periods": []},
    }

    if version is None:
        version = PageVersion(
            tenant_id=tenant_id,
            page_template_id=template.id,
            version=1,
            config_json=config_json,
            status=PageVersionStatus.published,
            created_by=created_by,
        )
        db.add(version)
        await db.flush()
        await db.refresh(version)
    return template, version


async def _ensure_campaign_and_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_name: str,
    benefit_name: str,
) -> tuple[Campaign, Benefit]:
    """幂等创建活动（draft 状态，便于挂权益）+ 可幂等领取的复购权益。"""
    result = await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.name == campaign_name))
    campaign = result.scalar_one_or_none()
    now = utcnow()
    start_at = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_at = (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rules_json = {
        "participation_conditions": "不限",
        "claim_limits": "每人限领1次",
        "validity_period": "领取后7天有效",
        "disclaimer": "最终解释权归品牌方所有",
        "minor_notice": "未成年人请在监护人陪同下参与",
        "customer_service_contact": "400-000-0000",
    }
    if campaign is None:
        campaign = Campaign(
            tenant_id=tenant_id,
            name=campaign_name,
            campaign_type="coupon",
            status="draft",
            start_at=start_at,
            end_at=end_at,
            rules_json=rules_json,
            description="基准验收活动：可幂等领取的复购权益。",
        )
        db.add(campaign)
        await db.flush()
        await db.refresh(campaign)

    result = await db.execute(select(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.name == benefit_name))
    benefit = result.scalar_one_or_none()
    if benefit is None:
        benefit = Benefit(
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            name=benefit_name,
            benefit_type="platform_coupon",
            config_json={"amount": 10, "min_order": 50},
            stock_total=100,
            stock_used=0,
            per_person_limit=1,
            status="active",
        )
        db.add(benefit)
        await db.flush()
        await db.refresh(benefit)
    return campaign, benefit


async def _ensure_assets(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    report_name: str,
    certificate_name: str,
) -> tuple[ProductAsset, ProductAsset]:
    """幂等创建 1 份检测报告 + 1 份资质证书（active），附在产品上。

    产品级资产是正确的权威业务对象（yimatong-zgb1.2 决策）；batch 级 attachment 留后续票。
    对照租户不建资产，保持最小隔离数据。
    """

    async def _ensure_one(name: str, asset_type: ProductAssetType, issuer: str, description: str) -> ProductAsset:
        result = await db.execute(
            select(ProductAsset).where(
                ProductAsset.tenant_id == tenant_id,
                ProductAsset.product_id == product_id,
                ProductAsset.name == name,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            asset = ProductAsset(
                tenant_id=tenant_id,
                product_id=product_id,
                asset_type=asset_type,
                name=name,
                description=description,
                issuer=issuer,
                status=ProductAssetStatus.active,
            )
            db.add(asset)
            await db.flush()
            await db.refresh(asset)
        return asset

    report = await _ensure_one(
        report_name,
        ProductAssetType.test_report,
        "基准检测机构",
        "基准验收用检测报告。",
    )
    certificate = await _ensure_one(
        certificate_name,
        ProductAssetType.certificate,
        "基准发证机关",
        "基准验收用资质证书。",
    )
    return report, certificate


async def _ensure_control_plane(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[_TenantSeedRef, _TenantSeedRef]:
    """Atomically ensure both tenant identities through the privileged adapter."""

    async with session_factory() as db, db.begin():
        await _prepare_control_session(db)
        base_tenant = await _get_or_create_tenant_identity(
            db,
            slug=BASELINE_TENANT_SLUG,
            name="基准租户",
            industry="食品",
        )
        control_tenant = await _get_or_create_tenant_identity(
            db,
            slug=CONTROL_TENANT_SLUG,
            name="隔离对照租户",
        )
        return (
            _TenantSeedRef(
                base_tenant.id,
                base_tenant.slug,
                BASELINE_ADMIN_EMAIL,
                "基准品牌管理员",
                BASELINE_ADMIN_PASSWORD,
            ),
            _TenantSeedRef(
                control_tenant.id,
                control_tenant.slug,
                CONTROL_ADMIN_EMAIL,
                "对照租户管理员",
                CONTROL_ADMIN_PASSWORD,
            ),
        )


async def _open_runtime_tenant(db: AsyncSession, tenant_ref: _TenantSeedRef) -> Tenant:
    """Enter one RLS scope, then take global shared -> Tenant locks."""

    await set_session_tenant_context(db, tenant_ref.tenant_id)
    await lock_quota_rollout_state(db)
    try:
        tenant = await require_active_plan(db, tenant_ref.tenant_id, lock_tenant=True)
    except TenantPlanExpiredError as exc:
        raise BaselineRecoveryRequired(
            f"Baseline tenant '{tenant_ref.tenant_slug}' plan is expired; renew it and rerun baseline build"
        ) from exc
    if tenant is None or tenant.slug != tenant_ref.tenant_slug:
        raise BaselineRecoveryRequired(
            f"Baseline tenant '{tenant_ref.tenant_slug}' disappeared after control-plane setup; rerun baseline build"
        )
    return tenant


async def _build_baseline_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ref: _TenantSeedRef,
) -> _BaselineFacts:
    try:
        async with session_factory() as db, db.begin():
            tenant = await _open_runtime_tenant(db, tenant_ref)
            admin = await _ensure_runtime_admin(db, tenant, tenant_ref)
            brand = await _ensure_brand(db, tenant.id, BRAND_NAME, "基准品牌：食品/农产品品牌方。")
            product = await _ensure_product(
                db,
                tenant.id,
                brand.id,
                PRODUCT_NAME,
                category="食品",
                origin="基准产地",
            )
            sku = await _ensure_sku(db, tenant.id, product.id, SKU_CODE, f"{PRODUCT_NAME}-{SKU_CODE}")
            production_batch = await _ensure_production_batch(
                db,
                tenant.id,
                product.id,
                sku.id,
                PRODUCTION_BATCH_CODE,
                PRODUCTION_BATCH_CODE,
            )
            code_batch, code_items = await _ensure_code_batch(
                db,
                tenant.id,
                product.id,
                sku.id,
                production_batch.id,
                CODE_BATCH_CODE,
                BASELINE_CODE_QUANTITY,
                admin.id,
            )
            template, version = await _ensure_page(
                db,
                tenant.id,
                product.id,
                PAGE_TEMPLATE_NAME,
                admin.id,
                BENEFIT_NAME,
            )
            campaign, benefit = await _ensure_campaign_and_benefit(
                db,
                tenant.id,
                CAMPAIGN_NAME,
                BENEFIT_NAME,
            )
            report, certificate = await _ensure_assets(
                db,
                tenant.id,
                product.id,
                REPORT_NAME,
                CERTIFICATE_NAME,
            )
            await refresh_quota_usage_from_authoritative_rows(db, tenant.id)
            first_active = next(
                (item for item in code_items if item.status == CodeItemStatus.activated),
                code_items[0] if code_items else None,
            )
            return _BaselineFacts(
                brand_id=brand.id,
                product_id=product.id,
                sku_id=sku.id,
                production_batch_id=production_batch.id,
                code_batch_id=code_batch.id,
                code_quantity=len(code_items),
                first_public_id=first_active.public_id if first_active else None,
                page_template_id=template.id,
                page_version_id=version.id,
                page_version_status=(version.status.value if hasattr(version.status, "value") else str(version.status)),
                campaign_id=campaign.id,
                benefit_id=benefit.id,
                report_id=report.id,
                certificate_id=certificate.id,
            )
    except BaselineRecoveryRequired:
        raise
    except Exception as exc:
        raise BaselineRecoveryRequired(
            f"Baseline business stage for '{tenant_ref.tenant_slug}' rolled back; "
            "control-plane tenant state is retained, fix the cause and rerun baseline build"
        ) from exc


async def _build_control_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ref: _TenantSeedRef,
) -> _ControlFacts:
    try:
        async with session_factory() as db, db.begin():
            tenant = await _open_runtime_tenant(db, tenant_ref)
            admin = await _ensure_runtime_admin(db, tenant, tenant_ref)
            brand = await _ensure_brand(db, tenant.id, CONTROL_BRAND_NAME, "对照品牌")
            product = await _ensure_product(
                db,
                tenant.id,
                brand.id,
                CONTROL_PRODUCT_NAME,
                category="食品",
                origin="对照产地",
            )
            sku = await _ensure_sku(
                db,
                tenant.id,
                product.id,
                CONTROL_SKU_CODE,
                f"{CONTROL_PRODUCT_NAME}-{CONTROL_SKU_CODE}",
            )
            production_batch = await _ensure_production_batch(
                db,
                tenant.id,
                product.id,
                sku.id,
                "PB-CTRL-001",
                "PB-CTRL-001",
            )
            _, code_items = await _ensure_code_batch(
                db,
                tenant.id,
                product.id,
                sku.id,
                production_batch.id,
                CONTROL_CODE_BATCH_CODE,
                CONTROL_CODE_QUANTITY,
                admin.id,
            )
            await refresh_quota_usage_from_authoritative_rows(db, tenant.id)
            return _ControlFacts(
                brand_id=brand.id,
                first_public_id=code_items[0].public_id if code_items else None,
            )
    except BaselineRecoveryRequired:
        raise
    except Exception as exc:
        raise BaselineRecoveryRequired(
            f"Baseline business stage for '{tenant_ref.tenant_slug}' rolled back; "
            "control-plane tenants and any completed tenant stage are retained, fix the cause and rerun baseline build"
        ) from exc


# ── 主流程 ────────────────────────────────────────────────────────────────


async def _build_baseline_dataset(
    database_url: str | None = None,
    control_database_url: str | None = None,
) -> dict[str, Any]:
    """构建完整基准数据集，返回稳定标识 → 关键字段映射（用于证据与摘要）。

    Args:
        database_url: tenant-runtime URL override. Defaults to
            ``settings.database_url``.
        control_database_url: privileged control URL override. When omitted
            alongside an explicit runtime override, the same URL is retained
            for backwards-compatible self-contained tests. The CLI path uses
            ``control_database_url``/``migration_database_url`` from settings.

    Control-plane tenant identities commit first as one transaction. Each
    tenant business graph then commits in its own RLS-scoped runtime
    transaction together with authoritative quota refresh. Cross-database-role
    atomicity is impossible; failures are classified as
    :class:`BaselineRecoveryRequired`, retain stable tenant identities and any
    completed tenant stage, and are recovered by an idempotent rerun.
    """
    runtime_url = _resolve_runtime_database_url(database_url)
    control_url = _resolve_control_database_url(database_url, control_database_url)
    runtime_factory, runtime_engine = _session_factory(runtime_url)
    control_factory, control_engine = _session_factory(control_url)
    try:
        base_ref, control_ref = await _ensure_control_plane(control_factory)
        base_facts = await _build_baseline_tenant(runtime_factory, base_ref)
        control_facts = await _build_control_tenant(runtime_factory, control_ref)
        return {
            "baseline_tenant": {
                "id": str(base_ref.tenant_id),
                "slug": base_ref.tenant_slug,
                "admin_email": BASELINE_ADMIN_EMAIL,
                "admin_password": BASELINE_ADMIN_PASSWORD,
            },
            "control_tenant": {
                "id": str(control_ref.tenant_id),
                "slug": control_ref.tenant_slug,
                "admin_email": CONTROL_ADMIN_EMAIL,
                "admin_password": CONTROL_ADMIN_PASSWORD,
            },
            "brand": {"id": str(base_facts.brand_id), "name": BRAND_NAME},
            "product": {"id": str(base_facts.product_id), "name": PRODUCT_NAME},
            "sku": {"id": str(base_facts.sku_id), "code": SKU_CODE},
            "production_batch": {
                "id": str(base_facts.production_batch_id),
                "batch_code": PRODUCTION_BATCH_CODE,
            },
            "code_batch": {
                "id": str(base_facts.code_batch_id),
                "batch_code": CODE_BATCH_CODE,
                "quantity": base_facts.code_quantity,
            },
            "first_public_id": base_facts.first_public_id,
            "page_template": {"id": str(base_facts.page_template_id), "name": PAGE_TEMPLATE_NAME},
            "page_version": {
                "id": str(base_facts.page_version_id),
                "status": base_facts.page_version_status,
            },
            "campaign": {"id": str(base_facts.campaign_id), "name": CAMPAIGN_NAME},
            "benefit": {"id": str(base_facts.benefit_id), "name": BENEFIT_NAME},
            "report": {"id": str(base_facts.report_id), "name": REPORT_NAME},
            "certificate": {"id": str(base_facts.certificate_id), "name": CERTIFICATE_NAME},
            "control_brand": {"id": str(control_facts.brand_id), "name": CONTROL_BRAND_NAME},
            "control_first_public_id": control_facts.first_public_id,
        }
    finally:
        await runtime_engine.dispose()
        await control_engine.dispose()


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        "Baseline dataset ready (yimatong-zgb1.1)",
        f"  baseline tenant: {result['baseline_tenant']['slug']} ({result['baseline_tenant']['id']})",
        f"  control  tenant: {result['control_tenant']['slug']} ({result['control_tenant']['id']})",
        f"  brand   : {result['brand']['name']} ({result['brand']['id']})",
        f"  product : {result['product']['name']} ({result['product']['id']})",
        f"  sku     : {result['sku']['code']} ({result['sku']['id']})",
        f"  pb      : {result['production_batch']['batch_code']} ({result['production_batch']['id']})",
        (
            f"  batch   : {result['code_batch']['batch_code']} "
            f"qty={result['code_batch']['quantity']} ({result['code_batch']['id']})"
        ),
        (
            f"  page    : {result['page_template']['name']} "
            f"version={result['page_version']['id']} status={result['page_version']['status']}"
        ),
        f"  campaign: {result['campaign']['name']} benefit={result['benefit']['name']}",
    ]
    if result["first_public_id"]:
        lines.append(f"  scan URL: /c/{result['first_public_id']}")
    bt = result["baseline_tenant"]
    lines.append(f"  login   : {bt['admin_email']} / {bt['admin_password']} (tenant_slug={bt['slug']})")
    return "\n".join(lines)


@app.command()
def build(
    json_out: bool = typer.Option(False, "--json", help="输出机器可读 JSON 摘要而非人类可读文本"),
    target: str = typer.Option(..., "--target", help="必须显式确认基准租户 slug"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许在 production 环境运行"),
):
    """幂等创建基准租户 + 对照租户 + 完整首条扫码旅程业务数据。"""

    _guard_baseline_build(target=target, allow_production=allow_production)
    result = asyncio.run(_build_baseline_dataset())
    if json_out:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(_format_summary(result))


@app.command()
def verify(
    json_out: bool = typer.Option(False, "--json", help="输出机器可读 JSON 证据"),
):
    """只读核对基准数据是否齐全（不修改数据）。返回 §12 结构化证据。"""

    from tests.test_acceptance.verifier import verify_baseline_presence

    async def _run() -> dict[str, Any]:
        control_url = _resolve_control_database_url()
        session_factory, engine = _session_factory(control_url)
        try:
            async with session_factory() as db, db.begin():
                # 验证器从平台视角跨租户只读核对（与 platform_admin 同一 RLS bypass 语义）
                await _prepare_control_session(db)
                return await verify_baseline_presence(db)
        finally:
            await engine.dispose()

    evidence = asyncio.run(_run())
    if json_out:
        typer.echo(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    else:
        status = evidence["status"].upper()
        typer.echo(f"[baseline verify] {status}")
        for k, v in evidence.get("db_assertions", {}).items():
            typer.echo(f"  {k}: {v}")
        if evidence.get("failure_reason"):
            typer.echo(f"  failure_reason: {evidence['failure_reason']}")


# 便于测试/Playwright 直接 import 调用的入口
def build_sync(database_url: str | None = None) -> dict[str, Any]:
    """同步包装：构建基准数据并返回摘要 dict。"""
    _guard_baseline_build(target=BASELINE_TENANT_SLUG, allow_production=False)
    return asyncio.run(_build_baseline_dataset(database_url))


# 提供给 verify 命令与测试复用的 utcnow 别名（避免 datetime.utcnow 弃用警告）
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
