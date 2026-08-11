"""行业模板 API 端点"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.services.industry_templates import ALL_TEMPLATES
from app.services.page import create_page_template, create_page_version
from app.utils.auth_rbac import require_permission, require_role

template_router = APIRouter(prefix="/api/v1/industry-templates", tags=["industry-templates"])


class IndustryTemplateApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID | None = None


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
    body: IndustryTemplateApplyRequest | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("page:create")),
):
    """将行业模板应用到租户，创建 page_template"""
    if template_id < 0 or template_id >= len(ALL_TEMPLATES):
        raise HTTPException(status_code=404, detail="Template not found")

    template_def = ALL_TEMPLATES[template_id]
    template = await create_page_template(
        db,
        tenant_id,
        template_def["name"],
        template_def["template_type"],
        template_def.get("description"),
        body.product_id if body else None,
        account_id,
    )
    version = await create_page_version(
        db,
        tenant_id,
        uuid.UUID(template["id"]),
        template_def["config_json"],
        account_id,
    )
    await db.commit()

    return {
        "template_id": template["id"],
        "version_id": version["id"] if version else None,
        "name": template["name"],
        "status": template["status"],
    }
