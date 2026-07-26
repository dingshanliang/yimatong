"""行业模板 API 端点"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.industry_templates import ALL_TEMPLATES
from app.utils.auth_rbac import require_role

template_router = APIRouter(prefix="/api/v1/industry-templates", tags=["industry-templates"])


@template_router.get("")
async def list_industry_templates(
    _role: str = Depends(require_role("admin", "operator")),
):
    """列出所有行业模板"""
    return [{"id": i, **t} for i, t in enumerate(ALL_TEMPLATES)]


@template_router.get("/{template_id}")
async def get_industry_template(
    template_id: int,
    _role: str = Depends(require_role("admin", "operator")),
):
    """获取单个行业模板详情"""
    if template_id < 0 or template_id >= len(ALL_TEMPLATES):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"id": template_id, **ALL_TEMPLATES[template_id]}


@template_router.post("/{template_id}/apply", status_code=201)
async def apply_industry_template(
    template_id: int,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    """将行业模板应用到租户，创建 page_template"""
    if template_id < 0 or template_id >= len(ALL_TEMPLATES):
        raise HTTPException(status_code=404, detail="Template not found")

    template_def = ALL_TEMPLATES[template_id]
    product_id = body.get("product_id") if body else None

    from app.models.page import PageTemplate

    tmpl = PageTemplate(
        tenant_id=tenant_id,
        name=template_def["name"],
        template_type=template_def["template_type"],
        product_id=uuid.UUID(product_id) if product_id else None,
        status="draft",
    )
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)

    # 创建初始版本
    from app.models.page import PageVersion, PageVersionStatus

    version = PageVersion(
        tenant_id=tenant_id,
        page_template_id=tmpl.id,
        version=1,
        config_json=template_def["config_json"],
        status=PageVersionStatus.draft,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)

    return {
        "template_id": str(tmpl.id),
        "version_id": str(version.id),
        "name": tmpl.name,
        "status": tmpl.status,
    }
