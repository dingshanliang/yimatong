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
from datetime import UTC, date, datetime, timedelta
from typing import Any

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.campaign import Benefit, Campaign
from app.models.code import CodeBatch, CodeItem, CodeItemStatus
from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus, TemplateType
from app.models.product import (
    SKU,
    Brand,
    Product,
    ProductAsset,
    ProductAssetStatus,
    ProductAssetType,
    ProductionBatch,
)
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.services.code import activate_batch, create_code_batch
from app.services.tenant import create_tenant
from app.utils import utcnow
from app.utils.security import hash_password

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


def _resolve_database_url(override: str | None = None) -> str:
    """解析本次执行使用的数据库 URL。

    优先级：显式 override > 当前 settings.database_url。
    baseline 不缓存 engine —— 测试可在调用前重设 `database_url` 环境变量并
    `importlib.reload(app.core.config)`，或直接传入 override。
    """
    return override or str(settings.database_url)


def _session_factory(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    url = _resolve_database_url(database_url)
    engine = create_async_engine(url)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── 平台级租户创建（绕过 RLS，与 platform.py 建租户端点同一受支持路径）────────


async def _get_or_create_tenant(
    db: AsyncSession,
    slug: str,
    name: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None = None,
) -> tuple[Tenant, Account]:
    """幂等获取或创建租户 + 默认组织 + admin 账号 + admin 角色。

    复刻 platform.py POST /tenants 的语义：在 bypass 会话内创建 Tenant、Organization、
    Account、Role(name='admin') 并关联。Role 不绑定 Permission 行 —— 中间件
    `_load_permissions` 兜底回落到 `get_permissions_for_role('admin')`，因此品牌管理员
    经 API 即具备 code:generate 等默认权限（这是受支持的产品路径，而非隐藏手工补丁）。
    """
    result = await db.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = await create_tenant(
            db,
            name=name,
            slug=slug,
            plan="free",
            admin_email=admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
            industry=industry,
        )
        await db.flush()

    # 确保默认组织存在
    result = await db.execute(select(Organization).where(Organization.tenant_id == tenant.id).limit(1))
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(tenant_id=tenant.id, name=f"{tenant.name} 默认组织")
        db.add(org)
        await db.flush()

    # 确保账号存在
    result = await db.execute(select(Account).where(Account.tenant_id == tenant.id, Account.email == admin_email))
    account = result.scalar_one_or_none()
    if account is None:
        account = Account(
            tenant_id=tenant.id,
            organization_id=org.id,
            email=admin_email,
            hashed_password=hash_password(admin_password),
            name=admin_name,
        )
        db.add(account)
        await db.flush()
    else:
        # 保持密码与组织可预期（二次运行不漂移）
        account.hashed_password = hash_password(admin_password)
        account.organization_id = org.id

    # 确保 admin 角色存在并关联
    result = await db.execute(select(Role).where(Role.tenant_id == tenant.id, Role.name == "admin"))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(tenant_id=tenant.id, name="admin", description="品牌管理员")
        db.add(role)
        await db.flush()

    result = await db.execute(
        account_roles.select().where(account_roles.c.account_id == account.id, account_roles.c.role_id == role.id)
    )
    if result.first() is None:
        await db.execute(account_roles.insert().values(account_id=account.id, role_id=role.id))

    await db.flush()
    await db.refresh(tenant)
    await db.refresh(account)
    return tenant, account


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


# ── 主流程 ────────────────────────────────────────────────────────────────


async def _build_baseline_dataset(database_url: str | None = None) -> dict[str, Any]:
    """构建完整基准数据集，返回稳定标识 → 关键字段映射（用于证据与摘要）。

    Args:
        database_url: 可选 DB URL 覆盖（测试用 testcontainer 时传入）；默认使用
            当前 `settings.database_url`。
    """
    session_factory = _session_factory(database_url)
    async with session_factory() as db:
        # 平台级：在 bypass 会话内建两个隔离租户。create_tenant service 自身会写
        # Tenant/Organization/Account；这里在同一 bypass 会话里补 Role + 关联。
        base_tenant, base_admin = await _get_or_create_tenant(
            db,
            slug=BASELINE_TENANT_SLUG,
            name="基准租户",
            admin_email=BASELINE_ADMIN_EMAIL,
            admin_name="基准品牌管理员",
            admin_password=BASELINE_ADMIN_PASSWORD,
            industry="食品",
        )
        control_tenant, control_admin = await _get_or_create_tenant(
            db,
            slug=CONTROL_TENANT_SLUG,
            name="隔离对照租户",
            admin_email=CONTROL_ADMIN_EMAIL,
            admin_name="对照租户管理员",
            admin_password=CONTROL_ADMIN_PASSWORD,
        )
        await db.flush()

        # ── 基准租户业务链 ──
        brand = await _ensure_brand(db, base_tenant.id, BRAND_NAME, "基准品牌：食品/农产品品牌方。")
        product = await _ensure_product(db, base_tenant.id, brand.id, PRODUCT_NAME, category="食品", origin="基准产地")
        sku = await _ensure_sku(db, base_tenant.id, product.id, SKU_CODE, f"{PRODUCT_NAME}-{SKU_CODE}")
        pb = await _ensure_production_batch(
            db, base_tenant.id, product.id, sku.id, PRODUCTION_BATCH_CODE, PRODUCTION_BATCH_CODE
        )
        code_batch, code_items = await _ensure_code_batch(
            db,
            base_tenant.id,
            product.id,
            sku.id,
            pb.id,
            CODE_BATCH_CODE,
            BASELINE_CODE_QUANTITY,
            base_admin.id,
        )
        template, version = await _ensure_page(
            db, base_tenant.id, product.id, PAGE_TEMPLATE_NAME, base_admin.id, BENEFIT_NAME
        )
        campaign, benefit = await _ensure_campaign_and_benefit(db, base_tenant.id, CAMPAIGN_NAME, BENEFIT_NAME)
        report, certificate = await _ensure_assets(db, base_tenant.id, product.id, REPORT_NAME, CERTIFICATE_NAME)

        # ── 对照租户：仅建立证明隔离所需的最少同名数据 ──
        c_brand = await _ensure_brand(db, control_tenant.id, CONTROL_BRAND_NAME, "对照品牌")
        c_product = await _ensure_product(
            db, control_tenant.id, c_brand.id, CONTROL_PRODUCT_NAME, category="食品", origin="对照产地"
        )
        c_sku = await _ensure_sku(
            db, control_tenant.id, c_product.id, CONTROL_SKU_CODE, f"{CONTROL_PRODUCT_NAME}-{CONTROL_SKU_CODE}"
        )
        c_pb = await _ensure_production_batch(
            db,
            control_tenant.id,
            c_product.id,
            c_sku.id,
            "PB-CTRL-001",
            "PB-CTRL-001",
        )
        c_batch, c_items = await _ensure_code_batch(
            db,
            control_tenant.id,
            c_product.id,
            c_sku.id,
            c_pb.id,
            CONTROL_CODE_BATCH_CODE,
            CONTROL_CODE_QUANTITY,
            control_admin.id,
        )

        await db.commit()

        first_active = next(
            (it for it in code_items if it.status == CodeItemStatus.activated), code_items[0] if code_items else None
        )
        return {
            "baseline_tenant": {
                "id": str(base_tenant.id),
                "slug": base_tenant.slug,
                "admin_email": BASELINE_ADMIN_EMAIL,
                "admin_password": BASELINE_ADMIN_PASSWORD,
            },
            "control_tenant": {
                "id": str(control_tenant.id),
                "slug": control_tenant.slug,
                "admin_email": CONTROL_ADMIN_EMAIL,
                "admin_password": CONTROL_ADMIN_PASSWORD,
            },
            "brand": {"id": str(brand.id), "name": brand.name},
            "product": {"id": str(product.id), "name": product.name},
            "sku": {"id": str(sku.id), "code": sku.code},
            "production_batch": {"id": str(pb.id), "batch_code": pb.batch_code},
            "code_batch": {
                "id": str(code_batch.id),
                "batch_code": code_batch.batch_code,
                "quantity": len(code_items),
            },
            "first_public_id": first_active.public_id if first_active else None,
            "page_template": {"id": str(template.id), "name": template.name},
            "page_version": {
                "id": str(version.id),
                "status": version.status.value if hasattr(version.status, "value") else str(version.status),
            },
            "campaign": {"id": str(campaign.id), "name": campaign.name},
            "benefit": {"id": str(benefit.id), "name": benefit.name},
            "report": {"id": str(report.id), "name": report.name},
            "certificate": {"id": str(certificate.id), "name": certificate.name},
            "control_brand": {"id": str(c_brand.id), "name": c_brand.name},
            "control_first_public_id": c_items[0].public_id if c_items else None,
        }


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
):
    """幂等创建基准租户 + 对照租户 + 完整首条扫码旅程业务数据。"""

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
        from sqlalchemy import text

        session_factory = _session_factory()
        async with session_factory() as db:
            # 验证器从平台视角跨租户只读核对（与 platform_admin 同一 RLS bypass 语义）
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            return await verify_baseline_presence(db)

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
    return asyncio.run(_build_baseline_dataset(database_url))


# 提供给 verify 命令与测试复用的 utcnow 别名（避免 datetime.utcnow 弃用警告）
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
