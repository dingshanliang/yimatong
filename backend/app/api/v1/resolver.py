"""码解析公开路由"""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.code import CodeItemStatus, CodeType
from app.services.page_render import render_page
from app.services.resolver import (
    INNER_VERIFY_PAGE,
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    OUTER_LANDING_PAGE,
    REVOKED_PAGE,
    RISK_FROZEN_PAGE,
    resolve_public_code,
)

resolver_router = APIRouter(tags=["resolver"])


@resolver_router.get("/c/{public_id}")
async def resolve_code_endpoint(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    data = await resolve_public_code(db, public_id)

    if not data:
        return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)

    status = data["status"]

    if status == CodeItemStatus.revoked:
        return HTMLResponse(content=REVOKED_PAGE, status_code=410)

    if status == CodeItemStatus.frozen:
        return HTMLResponse(content=RISK_FROZEN_PAGE, status_code=403)

    if status == CodeItemStatus.created:
        return HTMLResponse(content=NOT_ACTIVE_PAGE, status_code=200)

    code_type = data.get("code_type", CodeType.single)

    # 外码：展示引流页
    if code_type == CodeType.outer:
        return HTMLResponse(content=OUTER_LANDING_PAGE.format(public_id=public_id))

    # 内码：展示验真页
    if code_type == CodeType.inner:
        template_id = data.get("template_id")
        tenant_id = data.get("tenant_id")
        if template_id and tenant_id:
            html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
            if html:
                return HTMLResponse(content=html)
        return HTMLResponse(content=INNER_VERIFY_PAGE.format(public_id=public_id))

    # 单码：尝试渲染关联的页面模板
    template_id = data.get("template_id")
    tenant_id = data.get("tenant_id")

    if template_id and tenant_id:
        html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
        if html:
            return HTMLResponse(content=html)

    # 无绑定模板时返回默认页面
    return HTMLResponse(content=_build_code_page(data))


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
<p>码编号: {data['public_id']}</p>
<p>状态: {data['status']}</p>
</div>
</body>
</html>"""
