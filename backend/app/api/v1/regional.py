"""区域品牌/协会 API"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.regional import (
    add_member,
    authorize_product,
    create_code_rule,
    create_regional_org,
    create_shared_template,
    get_advanced_dashboard,
    get_regional_dashboard,
    get_whitelabel,
    list_code_rules,
    list_members,
    list_regional_orgs,
    list_shared_templates,
    set_whitelabel,
)

regional_router = APIRouter(prefix="/api/v1/regional", tags=["regional"])


class RegionalOrgCreate(BaseModel):
    name: str
    org_type: str = "association"


class MemberAdd(BaseModel):
    tenant_id: str
    member_name: str


class TemplateCreate(BaseModel):
    name: str
    config: dict


class ProductAuthCreate(BaseModel):
    product_id: str
    tenant_id: str


@regional_router.post("/orgs", status_code=201)
async def create_org_endpoint(
    body: RegionalOrgCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await create_regional_org(db, tenant_id, body.name, body.org_type)
    return {
        "id": str(org.id),
        "name": org.name,
        "org_type": org.org_type,
        "config": org.config,
    }


@regional_router.get("/orgs")
async def list_orgs_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orgs = await list_regional_orgs(db, tenant_id)
    return [{"id": str(o.id), "name": o.name, "org_type": o.org_type, "config": o.config} for o in orgs]


@regional_router.post("/orgs/{org_id}/members", status_code=201)
async def add_member_endpoint(
    org_id: uuid.UUID,
    body: MemberAdd,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    member = await add_member(db, org_id, uuid.UUID(body.tenant_id), body.member_name)
    return {
        "id": str(member.id),
        "org_id": str(member.org_id),
        "tenant_id": str(member.tenant_id),
        "member_name": member.member_name,
        "status": member.status,
    }


@regional_router.get("/orgs/{org_id}/members")
async def list_members_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    members = await list_members(db, org_id)
    return [
        {
            "id": str(m.id),
            "org_id": str(m.org_id),
            "tenant_id": str(m.tenant_id),
            "member_name": m.member_name,
            "status": m.status,
        }
        for m in members
    ]


@regional_router.post("/orgs/{org_id}/templates", status_code=201)
async def create_template_endpoint(
    org_id: uuid.UUID,
    body: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    template = await create_shared_template(db, org_id, body.name, body.config)
    return {
        "id": str(template.id),
        "org_id": str(template.org_id),
        "name": template.name,
        "config": template.config,
    }


@regional_router.get("/orgs/{org_id}/templates")
async def list_templates_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    templates = await list_shared_templates(db, org_id)
    return [{"id": str(t.id), "org_id": str(t.org_id), "name": t.name, "config": t.config} for t in templates]


@regional_router.post("/orgs/{org_id}/products", status_code=201)
async def authorize_product_endpoint(
    org_id: uuid.UUID,
    body: ProductAuthCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    auth = await authorize_product(db, org_id, uuid.UUID(body.product_id), uuid.UUID(body.tenant_id))
    return {
        "id": str(auth.id),
        "org_id": str(auth.org_id),
        "product_id": str(auth.product_id),
        "tenant_id": str(auth.tenant_id),
    }


@regional_router.get("/orgs/{org_id}/dashboard")
async def dashboard_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_regional_dashboard(db, org_id)


# W21: 区域品牌高级能力


class CodeRuleCreate(BaseModel):
    rule_name: str
    pattern: str
    prefix: str = ""


class WhitelabelUpdate(BaseModel):
    brand_name: str
    hide_yimatong: bool = False
    primary_color: str = "#000000"


@regional_router.post("/orgs/{org_id}/code-rules", status_code=201)
async def create_code_rule_endpoint(
    org_id: uuid.UUID,
    body: CodeRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await create_code_rule(db, org_id, body.rule_name, body.pattern, body.prefix)
    return {
        "id": str(rule.id),
        "org_id": str(rule.org_id),
        "rule_name": rule.rule_name,
        "pattern": rule.pattern,
        "prefix": rule.prefix,
    }


@regional_router.get("/orgs/{org_id}/code-rules")
async def list_code_rules_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await list_code_rules(db, org_id)
    return [{"id": str(r.id), "rule_name": r.rule_name, "pattern": r.pattern, "prefix": r.prefix} for r in rules]


@regional_router.get("/orgs/{org_id}/advanced-dashboard")
async def advanced_dashboard_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_advanced_dashboard(db, org_id)


@regional_router.put("/orgs/{org_id}/whitelabel")
async def set_whitelabel_endpoint(
    org_id: uuid.UUID,
    body: WhitelabelUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    config = await set_whitelabel(db, org_id, body.brand_name, body.hide_yimatong, body.primary_color)
    return {
        "id": str(config.id),
        "org_id": str(config.org_id),
        "brand_name": config.brand_name,
        "hide_yimatong": config.hide_yimatong,
        "primary_color": config.primary_color,
    }


@regional_router.get("/orgs/{org_id}/whitelabel")
async def get_whitelabel_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    config = await get_whitelabel(db, org_id)
    if not config:
        return {"brand_name": "", "hide_yimatong": False, "primary_color": "#000000"}
    return {
        "id": str(config.id),
        "org_id": str(config.org_id),
        "brand_name": config.brand_name,
        "hide_yimatong": config.hide_yimatong,
        "primary_color": config.primary_color,
    }
