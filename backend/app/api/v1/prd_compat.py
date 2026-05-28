"""PRD 路径兼容层 — 为 PRD 定义的路径创建别名路由"""

from fastapi import APIRouter, Depends, Request

# 单独的路由器，仅包含 PRD 兼容路径
prd_compat_router = APIRouter(tags=["prd-compat"])


# ---- /api/v1/public/events → 复用 /scan-events 逻辑 ----

from app.api.v1.scan_events import ScanEventRequest, report_scan_event
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

@prd_compat_router.post("/api/v1/public/events", status_code=201)
async def public_events_compat(
    request: Request,
    body: ScanEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """PRD 兼容路径：POST /api/v1/public/events"""
    return await report_scan_event(request, body, db)


# ---- /api/v1/public/leads → 复用 consumers/lead-capture 逻辑 ----

from app.api.v1.consumers import LeadCaptureRequest, lead_capture

@prd_compat_router.post("/api/v1/public/leads", status_code=201)
async def public_leads_compat(
    request: Request,
    body: LeadCaptureRequest,
    db: AsyncSession = Depends(get_db),
):
    """PRD 兼容路径：POST /api/v1/public/leads"""
    return await lead_capture(request, body, db)


# ---- /api/v1/public/benefits/{benefit_id}/claim → 复用 benefit-claims 逻辑 ----

from app.api.v1.benefit_claims import BenefitClaimRequest, claim_benefit_h5

@prd_compat_router.post("/api/v1/public/benefits/{benefit_id}/claim", status_code=201)
async def public_benefit_claim_compat(
    benefit_id: str,
    request: Request,
    body: BenefitClaimRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """PRD 兼容路径：POST /api/v1/public/benefits/{benefit_id}/claim"""
    if body is None:
        body = BenefitClaimRequest(benefit_id=benefit_id)
    else:
        body.benefit_id = benefit_id
    return await claim_benefit_h5(request, body, db)


# ---- /api/v1/risk/rules → 复用 risk-rules 逻辑 ----

import uuid
from pydantic import BaseModel
from app.core.dependencies import get_current_tenant
from app.services.risk_rule import (
    create_risk_rule as _create_rule,
    list_risk_rules as _list_rules,
    update_risk_rule as _update_rule,
)

class RiskRuleCreateCompat(BaseModel):
    name: str
    rule_type: str
    action: str
    config: dict

class RiskRuleUpdateCompat(BaseModel):
    name: str | None = None
    action: str | None = None
    config: dict | None = None

@prd_compat_router.get("/api/v1/risk/rules")
async def list_risk_rules_compat(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """PRD 兼容路径：GET /api/v1/risk/rules"""
    return await _list_rules(db, tenant_id)


@prd_compat_router.post("/api/v1/risk/rules", status_code=201)
async def create_risk_rule_compat(
    body: RiskRuleCreateCompat,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """PRD 兼容路径：POST /api/v1/risk/rules"""
    return await _create_rule(db, tenant_id, body.name, body.rule_type, body.action, body.config)


@prd_compat_router.patch("/api/v1/risk/rules/{rule_id}")
async def update_risk_rule_compat(
    rule_id: uuid.UUID,
    body: RiskRuleUpdateCompat,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """PRD 兼容路径：PATCH /api/v1/risk/rules/{rule_id}"""
    return await _update_rule(db, tenant_id, rule_id, **body.model_dump(exclude_none=True))


# ---- /api/v1/analytics/business-dashboard → 复用 dashboard 逻辑 ----

from app.services.analytics import get_dashboard

@prd_compat_router.get("/api/v1/analytics/business-dashboard")
async def business_dashboard_compat(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """PRD 兼容路径：GET /api/v1/analytics/business-dashboard"""
    return await get_dashboard(db, tenant_id)
