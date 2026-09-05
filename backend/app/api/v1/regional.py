"""区域品牌/协会 API

权限模型：与渠道管理共享既有权限码——写路由 channel:manage、读路由 channel:read
（admin/operator 均持有；viewer 与渠道门户身份均无，防止越权）。
"""

import uuid

from fastapi import APIRouter, Depends, Query
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
    publish_template_to_members,
    regional_org_summary,
    remove_member,
    set_whitelabel,
    update_member,
    verify_org_access,
)
from app.utils.auth_rbac import require_permission

regional_router = APIRouter(prefix="/api/v1/regional", tags=["regional"])


class RegionalOrgCreate(BaseModel):
    name: str
    org_type: str = "association"


class MemberAdd(BaseModel):
    tenant_id: str
    member_name: str


class MemberUpdate(BaseModel):
    member_name: str | None = None
    status: str | None = None


class TemplateCreate(BaseModel):
    name: str
    config: dict


class ProductAuthCreate(BaseModel):
    product_id: str
    tenant_id: str


# ── 组织 ──────────────────────────────────────────


@regional_router.post(
    "/orgs",
    status_code=201,
    summary="创建组织",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def create_org_endpoint(
    body: RegionalOrgCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await create_regional_org(db, tenant_id, body.name, body.org_type)
    return await regional_org_summary(db, org)


@regional_router.get("/orgs", summary="组织列表", dependencies=[Depends(require_permission("channel:read"))])
async def list_orgs_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orgs = await list_regional_orgs(db, tenant_id)
    return [await regional_org_summary(db, org) for org in orgs]


@regional_router.get("/orgs/{org_id}", summary="组织详情", dependencies=[Depends(require_permission("channel:read"))])
async def get_org_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await verify_org_access(db, org_id, tenant_id)
    return await regional_org_summary(db, org)


# ── 成员企业 ──────────────────────────────────────


@regional_router.post(
    "/orgs/{org_id}/members",
    status_code=201,
    summary="添加成员",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def add_member_endpoint(
    org_id: uuid.UUID,
    body: MemberAdd,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    member = await add_member(db, org_id, uuid.UUID(body.tenant_id), body.member_name)
    return {
        "id": str(member.id),
        "org_id": str(member.org_id),
        "tenant_id": str(member.tenant_id),
        "member_name": member.member_name,
        "status": member.status,
    }


@regional_router.get(
    "/orgs/{org_id}/members",
    summary="成员列表",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def list_members_endpoint(
    org_id: uuid.UUID,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    members, total = await list_members(db, org_id, status=status, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": str(m.id),
                "org_id": str(m.org_id),
                "tenant_id": str(m.tenant_id),
                "member_name": m.member_name,
                "status": m.status,
            }
            for m in members
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@regional_router.put(
    "/orgs/{org_id}/members/{member_id}",
    summary="更新成员",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def update_member_endpoint(
    org_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    member = await update_member(db, member_id, member_name=body.member_name, status=body.status)
    if not member:
        return {"error": "not found"}
    return {
        "id": str(member.id),
        "org_id": str(member.org_id),
        "tenant_id": str(member.tenant_id),
        "member_name": member.member_name,
        "status": member.status,
    }


@regional_router.delete(
    "/orgs/{org_id}/members/{member_id}",
    summary="移除成员",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def remove_member_endpoint(
    org_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    ok = await remove_member(db, member_id)
    return {"success": ok}


# ── 模板 ──────────────────────────────────────────


@regional_router.post(
    "/orgs/{org_id}/templates",
    status_code=201,
    summary="创建模板",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def create_template_endpoint(
    org_id: uuid.UUID,
    body: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    template = await create_shared_template(db, org_id, body.name, body.config)
    return {"id": str(template.id), "org_id": str(template.org_id), "name": template.name, "config": template.config}


@regional_router.get(
    "/orgs/{org_id}/templates",
    summary="模板列表",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def list_templates_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    templates = await list_shared_templates(db, org_id)
    return [{"id": str(t.id), "org_id": str(t.org_id), "name": t.name, "config": t.config} for t in templates]


@regional_router.post(
    "/orgs/{org_id}/templates/{template_id}/publish",
    summary="下发模板到成员企业",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def publish_template_endpoint(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    return await publish_template_to_members(db, org_id, template_id)


# ── 产品授权 ──────────────────────────────────────


@regional_router.post(
    "/orgs/{org_id}/products",
    status_code=201,
    summary="产品授权",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def authorize_product_endpoint(
    org_id: uuid.UUID,
    body: ProductAuthCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    auth = await authorize_product(db, org_id, uuid.UUID(body.product_id), uuid.UUID(body.tenant_id))
    return {
        "id": str(auth.id),
        "org_id": str(auth.org_id),
        "product_id": str(auth.product_id),
        "tenant_id": str(auth.tenant_id),
    }


# ── 看板 ──────────────────────────────────────────


@regional_router.get(
    "/orgs/{org_id}/dashboard",
    summary="汇总看板",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def dashboard_endpoint(
    org_id: uuid.UUID,
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    return await get_regional_dashboard(db, org_id, days_back=days_back)


# ── 高级能力 (W21) ────────────────────────────────


class CodeRuleCreate(BaseModel):
    rule_name: str
    pattern: str
    prefix: str = ""


class WhitelabelUpdate(BaseModel):
    brand_name: str
    hide_yimatong: bool = False
    primary_color: str = "#000000"


@regional_router.post(
    "/orgs/{org_id}/code-rules",
    status_code=201,
    summary="创建码规则",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def create_code_rule_endpoint(
    org_id: uuid.UUID,
    body: CodeRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    rule = await create_code_rule(db, org_id, body.rule_name, body.pattern, body.prefix)
    return {
        "id": str(rule.id),
        "org_id": str(rule.org_id),
        "rule_name": rule.rule_name,
        "pattern": rule.pattern,
        "prefix": rule.prefix,
    }


@regional_router.get(
    "/orgs/{org_id}/code-rules",
    summary="码规则列表",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def list_code_rules_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    rules = await list_code_rules(db, org_id)
    return [{"id": str(r.id), "rule_name": r.rule_name, "pattern": r.pattern, "prefix": r.prefix} for r in rules]


@regional_router.get(
    "/orgs/{org_id}/advanced-dashboard",
    summary="高级看板",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def advanced_dashboard_endpoint(
    org_id: uuid.UUID,
    days_back: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    return await get_advanced_dashboard(db, org_id, days_back=days_back)


@regional_router.put(
    "/orgs/{org_id}/whitelabel",
    summary="设置白标",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def set_whitelabel_endpoint(
    org_id: uuid.UUID,
    body: WhitelabelUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    config = await set_whitelabel(db, org_id, body.brand_name, body.hide_yimatong, body.primary_color)
    return {
        "id": str(config.id),
        "org_id": str(config.org_id),
        "brand_name": config.brand_name,
        "hide_yimatong": config.hide_yimatong,
        "primary_color": config.primary_color,
    }


@regional_router.get(
    "/orgs/{org_id}/whitelabel",
    summary="获取白标配置",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def get_whitelabel_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
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


# ── 统一营销活动管理 ──────────────────────────────


class UnifiedCampaignCreate(BaseModel):
    name: str
    description: str | None = None
    member_ids: list[str] | None = None  # 指定成员企业，空则全部


@regional_router.post(
    "/orgs/{org_id}/unified-campaigns",
    status_code=201,
    summary="创建统一活动",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def create_unified_campaign_endpoint(
    org_id: uuid.UUID,
    body: UnifiedCampaignCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.regional_campaign import create_unified_campaign

    campaign = await create_unified_campaign(db, org_id, tenant_id, body.name, body.description, body.member_ids)
    return campaign


@regional_router.get(
    "/orgs/{org_id}/unified-campaigns",
    summary="统一活动列表",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def list_unified_campaigns_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.regional_campaign import list_unified_campaigns

    return await list_unified_campaigns(db, org_id)


# ── 数据隔离策略 ──────────────────────────────────


class DataIsolationPolicy(BaseModel):
    """数据隔离策略：brand_all = 品牌方看全部，own_only = 只看自己"""

    scan_visibility: str = "own_only"  # own_only | brand_all
    claim_visibility: str = "own_only"  # own_only | brand_all
    member_data_visibility: str = "brand_all"  # own_only | brand_all


@regional_router.get(
    "/orgs/{org_id}/data-policy",
    summary="数据隔离策略",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def get_data_policy_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await verify_org_access(db, org_id, tenant_id)
    policy = org.config.get(
        "data_policy",
        {
            "scan_visibility": "own_only",
            "claim_visibility": "own_only",
            "member_data_visibility": "brand_all",
        },
    )
    return {"org_id": str(org_id), "policy": policy}


@regional_router.put(
    "/orgs/{org_id}/data-policy",
    summary="更新数据隔离策略",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def update_data_policy_endpoint(
    org_id: uuid.UUID,
    body: DataIsolationPolicy,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await verify_org_access(db, org_id, tenant_id)
    org.config["data_policy"] = body.model_dump()
    await db.flush()
    return {"org_id": str(org_id), "policy": body.model_dump()}


# ── 白标配置管理 ──────────────────────────────────


class WhitelabelConfigUpdate(BaseModel):
    brand_name: str | None = None
    hide_yimatong: bool | None = None
    primary_color: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    login_bg_url: str | None = None
    font_family: str | None = None
    custom_css: str | None = None


@regional_router.get(
    "/orgs/{org_id}/whitelabel-config",
    summary="获取白标配置（增强版）",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def get_whitelabel_config_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import get_whitelabel_config

    config = await get_whitelabel_config(db, org_id)
    if not config:
        return {
            "brand_name": "",
            "hide_yimatong": False,
            "primary_color": "#000000",
            "logo_url": None,
            "favicon_url": None,
            "login_bg_url": None,
            "font_family": "",
            "custom_css": None,
        }
    return {
        "id": str(config.id),
        "org_id": str(config.org_id),
        "brand_name": config.brand_name,
        "hide_yimatong": config.hide_yimatong,
        "primary_color": config.primary_color,
        "logo_url": config.logo_url,
        "favicon_url": config.favicon_url,
        "login_bg_url": config.login_bg_url,
        "font_family": config.font_family,
        "custom_css": config.custom_css,
    }


@regional_router.put(
    "/orgs/{org_id}/whitelabel-config",
    summary="更新白标配置",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def update_whitelabel_config_endpoint(
    org_id: uuid.UUID,
    body: WhitelabelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import update_whitelabel_config

    config = await update_whitelabel_config(db, org_id, **body.model_dump(exclude_none=True))
    return {
        "id": str(config.id),
        "org_id": str(config.org_id),
        "brand_name": config.brand_name,
        "hide_yimatong": config.hide_yimatong,
        "primary_color": config.primary_color,
        "logo_url": config.logo_url,
        "favicon_url": config.favicon_url,
        "login_bg_url": config.login_bg_url,
        "font_family": config.font_family,
        "custom_css": config.custom_css,
    }


# ── 域名管理 ──────────────────────────────────────


class DomainCreate(BaseModel):
    domain: str


@regional_router.post(
    "/orgs/{org_id}/domains",
    status_code=201,
    summary="添加自定义域名",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def add_domain_endpoint(
    org_id: uuid.UUID,
    body: DomainCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import add_domain

    td = await add_domain(db, tenant_id, body.domain)
    return {
        "id": str(td.id),
        "domain": td.domain,
        "verified": td.verified,
        "ssl_status": td.ssl_status,
        "cname_target": td.cname_target,
    }


@regional_router.get(
    "/orgs/{org_id}/domains",
    summary="域名列表",
    dependencies=[Depends(require_permission("channel:read"))],
)
async def list_domains_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import list_domains

    domains = await list_domains(db, tenant_id)
    return [
        {
            "id": str(d.id),
            "domain": d.domain,
            "verified": d.verified,
            "ssl_status": d.ssl_status,
            "cname_target": d.cname_target,
        }
        for d in domains
    ]


@regional_router.post(
    "/orgs/{org_id}/domains/{domain_id}/verify",
    summary="验证域名",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def verify_domain_endpoint(
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import verify_domain

    ok = await verify_domain(db, domain_id, tenant_id)
    return {"success": ok}


@regional_router.delete(
    "/orgs/{org_id}/domains/{domain_id}",
    status_code=204,
    summary="删除域名",
    dependencies=[Depends(require_permission("channel:manage"))],
)
async def remove_domain_endpoint(
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    await verify_org_access(db, org_id, tenant_id)
    from app.services.whitelabel import remove_domain

    if not await remove_domain(db, domain_id, tenant_id):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Domain not found")
