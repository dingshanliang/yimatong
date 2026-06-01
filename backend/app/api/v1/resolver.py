"""码解析公开路由"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import rate_limiter
from app.models.code import CodeItemStatus, CodeType
from app.models.page import PageVersion, PageVersionStatus
from app.services.page_render import render_page
from app.services.public_id import validate_public_id
from app.services.redis_cache import AsyncRedisCache
from app.services.resolve_cache import resolve_cache
from app.services.resolver import (
    INNER_VERIFY_PAGE,
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    OUTER_LANDING_PAGE,
    REVOKED_PAGE,
    RISK_FROZEN_PAGE,
    resolve_public_code,
)
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import create_scan_token

resolver_router = APIRouter(tags=["resolver"])

_product_cache = AsyncRedisCache(prefix="product", default_ttl=600)
_page_config_cache = AsyncRedisCache(prefix="pagecfg", default_ttl=600)


@resolver_router.get("/c/{public_id}", summary="解析 码")
async def resolve_code_endpoint(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept

    # 1. 限流检查
    client_ip = request.client.host if request.client else "unknown"
    rate_result = rate_limiter.check_resolver(client_ip, public_id)
    if not rate_result.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    # 2. public_id 格式校验（Luhn）
    if not validate_public_id(public_id):
        if want_json:
            return JSONResponse(status_code=404, content={"detail": "not_found"})
        return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)

    # 3. 缓存查询
    cached = await resolve_cache.get(f"resolve:{public_id}")
    if cached:
        data = cached
    else:
        data = await resolve_public_code(db, public_id)
        if data:
            await resolve_cache.set(f"resolve:{public_id}", data)

    if not data:
        if want_json:
            return JSONResponse(status_code=404, content={"detail": "not_found"})
        return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)

    status = data["status"]

    # 错误状态统一处理
    if status == CodeItemStatus.revoked:
        if want_json:
            return JSONResponse(status_code=410, content={"code_data": {"status": "revoked", "public_id": public_id}})
        return HTMLResponse(content=REVOKED_PAGE, status_code=410)

    if status == CodeItemStatus.frozen:
        if want_json:
            return JSONResponse(status_code=403, content={"code_data": {"status": "frozen", "public_id": public_id}})
        return HTMLResponse(content=RISK_FROZEN_PAGE, status_code=403)

    if status == CodeItemStatus.created:
        if want_json:
            return JSONResponse(content={"code_data": {"status": "not_active", "public_id": public_id}})
        return HTMLResponse(content=NOT_ACTIVE_PAGE, status_code=200)

    # 4. 记录扫码事件（仅对已激活的码）
    scan_info = {"is_first_scan": False, "scan_count": 0}
    if status == CodeItemStatus.activated:
        user_agent = request.headers.get("user-agent")
        ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None
        try:
            event = await record_scan_event(
                db=db,
                tenant_id=uuid.UUID(data["tenant_id"]),
                public_id=public_id,
                ip_hash=ip_hash,
                user_agent=user_agent,
                environment=parse_environment(user_agent),
            )
            scan_info["is_first_scan"] = event.is_first_scan
        except Exception:
            pass

    # 5. 生成 scan_token
    ip_hash_val = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else ""
    scan_token = create_scan_token(public_id, ip_hash_val)

    # 6. JSON 响应模式（H5 前端使用）
    if want_json:
        resp = await _build_json_response(db, data, scan_token, scan_info)
        return JSONResponse(content=resp)

    # --- HTML 响应模式（向后兼容 / 直连浏览器） ---
    code_type = data.get("code_type", CodeType.single)

    if code_type == CodeType.outer:
        return HTMLResponse(content=OUTER_LANDING_PAGE.format(public_id=public_id))

    if code_type == CodeType.inner:
        template_id = data.get("template_id")
        tenant_id = data.get("tenant_id")
        if template_id and tenant_id:
            html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
            if html:
                return HTMLResponse(content=html)
        return HTMLResponse(content=INNER_VERIFY_PAGE.format(public_id=public_id))

    template_id = data.get("template_id")
    tenant_id = data.get("tenant_id")
    if template_id and tenant_id:
        html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
        if html:
            return HTMLResponse(content=html)

    return HTMLResponse(content=_build_code_page(data))


async def _build_json_response(
    db: AsyncSession,
    data: dict,
    scan_token: str,
    scan_info: dict,
) -> dict:
    """构建 H5 前端所需的 JSON 响应"""
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
        from app.models.product import Brand, Product

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
                    "images": product.images if hasattr(product, "images") and product.images else [],
                }
                brand_data = {"name": brand.name, "logo_url": brand.logo_url or ""}
                await _product_cache.set(f"pb:{product_id}", {
                    "product": product_data,
                    "brand": brand_data,
                })
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

    # 查询当前产品可用活动，供 H5 展示权益与活动规则
    if product_id:
        from app.models.campaign import Benefit, Campaign, CampaignStatus

        campaign_result = await db.execute(
            select(Campaign)
            .where(
                Campaign.product_id == uuid.UUID(product_id),
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
            result["campaign"] = {
                "id": str(campaign.id),
                "name": campaign.name,
                "rules": campaign.rules_json,
                "benefit": {
                    "id": str(benefit.id),
                    "name": benefit.name,
                    "benefit_type": benefit.benefit_type,
                    "description": benefit.config_json.get("description"),
                }
                if benefit
                else None,
            }

    return result


def _build_code_page(data: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品信息</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; }}
.info {{ background: #f5f5f5; padding: 12px; border-radius: 8px; margin-top: 12px; }}
</style>
</head>
<body>
<h2>产品信息</h2>
<div class="info">
<p>码编号: {data["public_id"]}</p>
<p>状态: {data["status"]}</p>
</div>
</body>
</html>"""
