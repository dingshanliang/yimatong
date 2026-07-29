"""页面模板与版本 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.industry_templates import ALL_TEMPLATES
from app.services.page import (
    VersionImmutableError,
    VersionStateError,
    archive_page_version,
    create_page_template,
    create_page_version,
    delete_page_template,
    get_page_template,
    list_page_templates,
    list_page_versions,
    publish_page_version,
    rollback_page_version,
    update_page_template,
    update_page_version,
)
from app.services.page_render import render_page
from app.utils.auth_rbac import require_role

page_template_router = APIRouter(prefix="/api/v1/page-templates", tags=["page-templates"])
page_version_router = APIRouter(prefix="/api/v1/page-versions", tags=["page-versions"])


class PageTemplateCreateRequest(BaseModel):
    name: str
    template_type: str
    description: str | None = None
    product_id: uuid.UUID | None = None


class PageTemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class IndustryTemplateCloneRequest(BaseModel):
    name: str | None = None
    product_id: uuid.UUID | None = None


class PageVersionCreateRequest(BaseModel):
    config_json: dict

    @field_validator("config_json")
    @classmethod
    def validate_config_size(cls, v: dict) -> dict:
        import json

        if len(json.dumps(v, ensure_ascii=False)) > 100_000:
            raise ValueError("config_json 太大，最大允许 100KB")
        return v

    @field_validator("config_json")
    @classmethod
    def validate_brand_theme_slots(cls, v: dict) -> dict:
        from app.utils.brand_color import validate_brand_theme

        return validate_brand_theme(v)


class PageVersionUpdateRequest(BaseModel):
    config_json: dict

    @field_validator("config_json")
    @classmethod
    def validate_config_size(cls, v: dict) -> dict:
        import json

        if len(json.dumps(v, ensure_ascii=False)) > 100_000:
            raise ValueError("config_json 太大，最大允许 100KB")
        return v

    @field_validator("config_json")
    @classmethod
    def validate_brand_theme_slots(cls, v: dict) -> dict:
        from app.utils.brand_color import validate_brand_theme

        return validate_brand_theme(v)


@page_template_router.get("/industry-templates")
async def list_industry_templates(
    _role: str = Depends(require_role("admin", "operator")),
):
    """获取行业模板库"""
    return ALL_TEMPLATES


@page_template_router.post("/industry-templates/{index}/clone", status_code=201)
async def clone_industry_template(
    index: int,
    body: IndustryTemplateCloneRequest | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    """从行业模板库一键复制创建新页面"""
    if index < 0 or index >= len(ALL_TEMPLATES):
        raise HTTPException(status_code=404, detail="Template not found")
    tpl = ALL_TEMPLATES[index]
    template = await create_page_template(
        db,
        tenant_id,
        body.name if body and body.name else tpl["name"],
        tpl["template_type"],
        tpl["description"],
        body.product_id if body else None,
    )
    version = await create_page_version(
        db,
        tenant_id,
        uuid.UUID(template["id"]),
        tpl["config_json"],
        account_id,
    )
    return {"template": template, "version": version}


@page_template_router.post("", status_code=201, summary="创建 页面模板")
async def create_page_template_endpoint(
    body: PageTemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    return await create_page_template(
        db,
        tenant_id,
        body.name,
        body.template_type,
        body.description,
        body.product_id,
    )


@page_template_router.get("", summary="页面模板 列表")
async def list_page_templates_endpoint(
    template_type: str | None = Query(None),
    status: str | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    items, total = await list_page_templates(
        db,
        tenant_id,
        template_type=template_type,
        status=status,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@page_template_router.get("/{template_id}", summary="获取 页面模板")
async def get_page_template_endpoint(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    data = await get_page_template(db, tenant_id, template_id)
    if not data:
        raise HTTPException(status_code=404, detail="Page template not found")
    return data


@page_template_router.patch("/{template_id}", summary="更新 页面模板")
async def update_page_template_endpoint(
    template_id: uuid.UUID,
    body: PageTemplateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    data = await update_page_template(
        db,
        tenant_id,
        template_id,
        name=body.name,
        description=body.description,
    )
    if not data:
        raise HTTPException(status_code=404, detail="Page template not found")
    return data


@page_template_router.delete("/{template_id}", summary="删除 页面模板")
async def delete_page_template_endpoint(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    deleted = await delete_page_template(db, tenant_id, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Page template not found")
    return {"status": "archived"}


@page_template_router.get("/{template_id}/preview", response_class=HTMLResponse)
async def preview_page_template_endpoint(
    template_id: uuid.UUID,
    mock_brand: str | None = Query(None),
    version_id: uuid.UUID | None = Query(None, description="指定版本 ID 用于草稿预览"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    context = {}
    if mock_brand:
        context["mock_brand"] = mock_brand
    html = await render_page(
        db,
        tenant_id,
        template_id,
        context=context or None,
        version_id=version_id,
    )
    if not html:
        detail = "Version not found" if version_id else "No published version found"
        raise HTTPException(status_code=404, detail=detail)
    return HTMLResponse(content=html)


@page_template_router.post("/{template_id}/versions", status_code=201, summary="创建 页面版本")
async def create_page_version_endpoint(
    template_id: uuid.UUID,
    body: PageVersionCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    try:
        return await create_page_version(
            db,
            tenant_id,
            template_id,
            body.config_json,
            account_id,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e


@page_template_router.get("/{template_id}/versions", summary="页面版本 列表")
async def list_page_versions_endpoint(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    return await list_page_versions(db, tenant_id, template_id)


@page_version_router.patch("/{version_id}", summary="更新 页面版本")
async def update_page_version_endpoint(
    version_id: uuid.UUID,
    body: PageVersionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    try:
        data = await update_page_version(db, tenant_id, version_id, body.config_json)
    except VersionImmutableError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    if not data:
        raise HTTPException(status_code=404, detail="Page version not found")
    return data


@page_version_router.post("/{version_id}/publish")
async def publish_page_version_endpoint(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    try:
        data = await publish_page_version(db, tenant_id, version_id)
    except VersionStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not data:
        raise HTTPException(status_code=404, detail="Page version not found")
    from app.services.page_render import invalidate_cache

    await invalidate_cache(tenant_id, uuid.UUID(data["page_template_id"]))
    from app.services.audit import write_audit_log

    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "page_published",
        f"page_template:{data['page_template_id']}",
        {
            "version_id": str(version_id),
            "before": "draft",
            "after": "published",
            "result": "success",
        },
    )
    return data


@page_version_router.post("/{version_id}/archive")
async def archive_page_version_endpoint(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    try:
        data = await archive_page_version(db, tenant_id, version_id)
    except VersionStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not data:
        raise HTTPException(status_code=404, detail="Page version not found")
    from app.services.page_render import invalidate_cache

    await invalidate_cache(tenant_id, uuid.UUID(data["page_template_id"]))
    return data


@page_template_router.post("/{template_id}/versions/{version_id}/rollback", status_code=201)
async def rollback_page_version_endpoint(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    data = await rollback_page_version(
        db,
        tenant_id,
        template_id,
        version_id,
        account_id,
    )
    if not data:
        raise HTTPException(status_code=404, detail="Target version not found")
    from app.services.page_render import invalidate_cache

    await invalidate_cache(tenant_id, template_id)
    return data
