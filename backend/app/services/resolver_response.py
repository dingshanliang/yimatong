"""构建 H5 前端所需的 JSON 响应（从 resolver 模块提取）"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.page import PageVersion, PageVersionStatus
from app.models.product import Brand, Product, ProductionBatch
from app.services.redis_cache import AsyncRedisCache

_product_cache = AsyncRedisCache(prefix="product", default_ttl=600)
_page_config_cache = AsyncRedisCache(prefix="pagecfg", default_ttl=600)


async def build_json_response(
    db: AsyncSession,
    data: dict,
    scan_token: str,
    scan_info: dict,
) -> dict:
    """构建 H5 前端所需的 JSON 响应

    查询品牌、页面配置、生产批次溯源、活动权益等信息。
    """
    uuid.UUID(data["tenant_id"])
    product_id = data.get("product_id")

    result: dict = {
        "scan_token": scan_token,
        "code_data": {
            "public_id": data["public_id"],
            "status": data["status"],
            "code_type": data.get("code_type", "single"),
        },
        "scan_info": scan_info,
    }

    # 查询品牌信息（缓存优先）
    if product_id:
        cached_pb = await _product_cache.get(f"pb:{product_id}")
        if cached_pb:
            result["code_data"]["product"] = cached_pb["product"]
            if cached_pb.get("brand"):
                result["brand"] = cached_pb["brand"]
                result["tenant_branding"] = cached_pb["brand"]
        else:
            prod_result = await db.execute(
                select(Product, Brand)
                .join(Brand, Product.brand_id == Brand.id)
                .where(Product.id == uuid.UUID(product_id))
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
                await _product_cache.set(
                    f"pb:{product_id}",
                    {
                        "product": product_data,
                        "brand": brand_data,
                    },
                )
                result["code_data"]["product"] = product_data
                result["brand"] = brand_data
                result["tenant_branding"] = brand_data

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
                    PageVersion.status == PageVersionStatus.published,
                )
                .limit(1)
            )
            version = ver_result.scalar_one_or_none()
            if version:
                await _page_config_cache.set(f"pv:{template_id}", version.config_json)
                result["page_config"] = version.config_json

    # 溯源信息：使用 resolver 已传递的 production_batch_id，避免重复查 CodeBatch
    production_batch_id = data.get("production_batch_id")
    if production_batch_id:
        pb_result = await db.execute(
            select(ProductionBatch).where(ProductionBatch.id == uuid.UUID(production_batch_id))
        )
        prod_batch = pb_result.scalar_one_or_none()
        if prod_batch:
            result["batch"] = {
                "batch_code": prod_batch.batch_code,
                "production_date": str(prod_batch.production_date),
                "expiry_date": str(prod_batch.expiry_date),
                "origin": prod_batch.origin or "",
            }

    # 查询当前产品可用活动，供 H5 展示权益与活动规则
    if product_id:
        tenant_uuid = uuid.UUID(data["tenant_id"])
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
                    "description": (
                        config.get("description") if isinstance(config, dict) else None
                    ),
                }
                if benefit
                else None,
            }

    return result
