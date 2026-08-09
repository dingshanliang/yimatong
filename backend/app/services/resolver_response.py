"""构建 H5 前端所需的 JSON 响应（从 resolver 模块提取）"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.code import to_lifecycle
from app.models.page import PageVersion, PageVersionStatus
from app.models.product import (
    Brand,
    Product,
    ProductAsset,
    ProductAssetStatus,
    ProductAssetType,
    ProductionBatch,
)
from app.models.risk import RiskAlert
from app.models.tenant import Tenant
from app.services.redis_cache import AsyncRedisCache

_product_cache = AsyncRedisCache(prefix="product", default_ttl=600)
_page_config_cache = AsyncRedisCache(prefix="pagecfg", default_ttl=600)


async def invalidate_product_cache(product_id: uuid.UUID | str) -> None:
    """权威产品/批次/资产编辑后失效公共解析缓存（yimatong-zgb1.2 AC：编辑后立即可见）。

    Redis 不可用时静默降级（与 AsyncRedisCache 一致）。
    """
    await _product_cache.invalidate(f"pb:{product_id}")


async def invalidate_page_config_cache(template_id: uuid.UUID | str) -> None:
    """页面版本编辑/发布后失效页面配置缓存。"""
    await _page_config_cache.invalidate(f"pv:{template_id}")


async def _fetch_public_assets(
    db: AsyncSession, tenant_uuid: uuid.UUID, product_id: uuid.UUID
) -> tuple[list[dict], list[dict]]:
    """取该产品下 active 的检测报告与资质证书，转为公开字段。

    只返回消费者可见的公开字段；不返回 tenant_id 等内部信息。
    缺失返回空数组（不虚构、不占位）。
    """
    asset_result = await db.execute(
        select(ProductAsset)
        .where(
            ProductAsset.tenant_id == tenant_uuid,
            ProductAsset.product_id == product_id,
            ProductAsset.status == ProductAssetStatus.active,
            ProductAsset.asset_type.in_((ProductAssetType.test_report, ProductAssetType.certificate)),
        )
        .order_by(ProductAsset.created_at.desc())
    )
    reports: list[dict] = []
    certificates: list[dict] = []
    for asset in asset_result.scalars():
        item = {
            "id": str(asset.id),
            "name": asset.name,
            "issuer": asset.issuer or "",
            "valid_until": str(asset.valid_until) if asset.valid_until else "",
            "file_url": asset.file_url or "",
            "image_url": asset.image_url or "",
            "summary": asset.description or "",
        }
        if asset.asset_type == ProductAssetType.test_report:
            reports.append(item)
        else:
            certificates.append(item)
    return reports, certificates


async def build_json_response(
    db: AsyncSession,
    data: dict,
    scan_token: str,
    scan_info: dict,
) -> dict:
    """构建 H5 前端所需的 JSON 响应

    查询品牌、页面配置、生产批次溯源、检测报告/资质证书、活动权益等信息。

    安全约束（yimatong-zgb1.2）：
    - 所有权威资料查询都带 ``tenant_id`` 过滤，防御性保证不会串读其他租户的产品/批次/资产。
    - 资产只返回 ``status=active`` 的公开字段；缺失返回空数组而非占位。
    """
    tenant_uuid = uuid.UUID(data["tenant_id"])
    product_id = data.get("product_id")

    result: dict = {
        "scan_token": scan_token,
        "code_data": {
            "public_id": data["public_id"],
            "status": data["status"],
            "code_type": data.get("code_type", "single"),
            # 权威四状态生命周期（yimatong-zgb1.3）：旧 status 经 to_lifecycle 映射。
            # 消费者层契约字段；旧 status 保留兼容（AC4）。
            "lifecycle": to_lifecycle(data["status"]).value,
            # 默认空数组：缺失权威数据时不虚构（yimatong-zgb1.2 AC）。
            # 找到产品/批次/资产后会被覆盖。
            "test_reports": [],
            "certificates": [],
        },
        "scan_info": scan_info,
    }

    # 查询品牌+产品+资产信息（缓存优先）。缓存与权威编辑路径联动失效
    # （见 services.product.update_product / update_production_batch / *_product_asset）。
    if product_id:
        cached_pb = await _product_cache.get(f"pb:{product_id}")
        if cached_pb:
            result["code_data"]["product"] = cached_pb["product"]
            if cached_pb.get("brand"):
                result["brand"] = cached_pb["brand"]
                result["tenant_branding"] = cached_pb["brand"]
            result["code_data"]["test_reports"] = cached_pb.get("test_reports", [])
            result["code_data"]["certificates"] = cached_pb.get("certificates", [])
        else:
            # 防御性 tenant 过滤：即使 product_id 来自上游 CodeItem，也在此校验归属
            prod_result = await db.execute(
                select(Product, Brand)
                .join(Brand, Product.brand_id == Brand.id)
                .where(Product.id == uuid.UUID(product_id), Product.tenant_id == tenant_uuid)
            )
            row = prod_result.one_or_none()
            if row:
                product, brand = row
                product_data = {
                    "name": product.name,
                    "description": product.description,
                    "image_url": product.image_url or "",
                    "origin": product.origin or "",
                }
                brand_data = {"name": brand.name, "logo_url": brand.logo_url or ""}
                test_reports, certificates = await _fetch_public_assets(db, tenant_uuid, product.id)
                await _product_cache.set(
                    f"pb:{product_id}",
                    {
                        "product": product_data,
                        "brand": brand_data,
                        "test_reports": test_reports,
                        "certificates": certificates,
                    },
                )
                result["code_data"]["product"] = product_data
                result["brand"] = brand_data
                result["tenant_branding"] = brand_data
                result["code_data"]["test_reports"] = test_reports
                result["code_data"]["certificates"] = certificates

    # 租户级品牌定制槽位（yimatong-z6i0.10 / ADR-0001）：并入 tenant_branding，
    # H5 按「页面 DSL brand_theme → 租户 brand_profile → 默认主题」三层回退解析。
    # brand_profile 含五槽位（primary_color/radius_preset/background_preset/
    # hide_yimatong_brand/logo_url）。
    # 合并顺序：租户 brand_profile 优先于产品品牌 brand_data（ADR-0001 决策意图：
    # 品牌方在 Admin 自助设置的 Logo 必须生效，不被产品品牌 logo 覆盖）。
    tenant_result = await db.execute(
        select(Tenant.brand_profile, Tenant.enabled_features).where(Tenant.id == tenant_uuid)
    )
    tenant_brand_row = tenant_result.one_or_none()
    brand_profile = dict(tenant_brand_row.brand_profile or {}) if tenant_brand_row else {}
    if brand_profile.get("hide_yimatong_brand"):
        from app.services.entitlement import is_feature_enabled

        if not is_feature_enabled(tenant_brand_row.enabled_features, "white_label"):
            brand_profile["hide_yimatong_brand"] = False
    if brand_profile:
        existing = result.get("tenant_branding") or {}
        # logo_url：租户配了就用租户的；没配才回退到产品品牌 logo
        if not brand_profile.get("logo_url") and existing.get("logo_url"):
            existing = {**existing, "logo_url": existing["logo_url"]}
        result["tenant_branding"] = {**existing, **brand_profile}

    # 查询页面配置（缓存优先）
    template_id = data.get("template_id")
    if template_id:
        cached_config = await _page_config_cache.get(f"pv:{template_id}")
        if cached_config:
            result["page_config"] = cached_config
        else:
            ver_result = await db.execute(
                select(PageVersion)
                .where(
                    PageVersion.page_template_id == uuid.UUID(template_id),
                    PageVersion.tenant_id == tenant_uuid,
                    PageVersion.status == PageVersionStatus.published,
                )
                .limit(1)
            )
            version = ver_result.scalar_one_or_none()
            if version:
                await _page_config_cache.set(f"pv:{template_id}", version.config_json)
                result["page_config"] = version.config_json

    # 溯源信息：使用 resolver 已传递的 production_batch_id，避免重复查 CodeBatch。
    # 同时写入 code_data.batch（H5 契约位置）与顶层 batch（兼容既有客户端/测试）。
    production_batch_id = data.get("production_batch_id")
    if production_batch_id:
        pb_result = await db.execute(
            select(ProductionBatch).where(
                ProductionBatch.id == uuid.UUID(production_batch_id),
                ProductionBatch.tenant_id == tenant_uuid,
            )
        )
        prod_batch = pb_result.scalar_one_or_none()
        if prod_batch:
            batch_data = {
                "batch_code": prod_batch.batch_code,
                "production_date": str(prod_batch.production_date),
                "expiry_date": str(prod_batch.expiry_date),
                "origin": prod_batch.origin or "",
            }
            result["batch"] = batch_data  # 兼容顶层键
            result["code_data"]["batch"] = batch_data  # H5 契约位置

    # 查询当前产品可用活动，供 H5 展示权益与活动规则
    if product_id:
        campaign_result = await db.execute(
            select(Campaign)
            .where(
                Campaign.product_id == uuid.UUID(product_id),
                Campaign.tenant_id == tenant_uuid,
                Campaign.status == CampaignStatus.ACTIVE,
            )
            .order_by(Campaign.id.desc())
            .limit(1)
        )
        campaign = campaign_result.scalar_one_or_none()
        if campaign:
            benefit_result = await db.execute(
                select(Benefit)
                .where(Benefit.campaign_id == campaign.id, Benefit.tenant_id == campaign.tenant_id)
                .order_by(Benefit.id.desc())
                .limit(1)
            )
            benefit = benefit_result.scalar_one_or_none()
            config = benefit.config_json if benefit else None
            result["campaign"] = {
                "id": str(campaign.id),
                "name": campaign.name,
                "rules": campaign.rules_json,
                "benefit": {
                    "id": str(benefit.id),
                    "name": benefit.name,
                    "benefit_type": benefit.benefit_type,
                    "config_json": benefit.config_json,
                    "description": (config.get("description") if isinstance(config, dict) else None),
                }
                if benefit
                else None,
            }

    # yimatong-zgb1.7 AC2：异常码保留溯源 + 谨慎风险提示（不宣告假货，Decision 16）。
    # 查询该 public_id 的 active medium/high RiskAlert，写入 scan_info.risk_warning。
    risk_alert_result = await db.execute(
        select(RiskAlert.risk_level, RiskAlert.alert_type, RiskAlert.rule_name)
        .where(
            RiskAlert.tenant_id == tenant_uuid,
            RiskAlert.public_id == data["public_id"],
            RiskAlert.resolved.is_(False),
            RiskAlert.risk_level.in_(["medium", "high"]),
        )
        .order_by(RiskAlert.created_at.desc())
        .limit(1)
    )
    risk_row = risk_alert_result.one_or_none()
    if risk_row:
        scan_info["risk_warning"] = {
            "level": risk_row[0],  # risk_level
            "alert_type": risk_row[1],
            "rule_name": risk_row[2],
            "message": "该码存在异常使用信号，请审慎对待。如非本人操作，请联系品牌客服。",
            "support_path": "/support",
            # Decision 16：不宣告假货，只说明存在风险信号
            "is_counterfeit": False,
        }

    return result
