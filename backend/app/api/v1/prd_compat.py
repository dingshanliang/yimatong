"""PRD 路径兼容层 — 为 PRD 定义的路径创建别名路由"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

# 单独的路由器，仅包含 PRD 兼容路径
prd_compat_router = APIRouter(tags=["prd-compat"])


# ---- /api/v1/public/events → 复用 /scan-events 逻辑 ----

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.scan_events import ScanEventRequest, report_scan_event
from app.core.database import get_db, get_db_for_consumer
from app.utils.auth_rbac import require_permission


@prd_compat_router.post("/api/v1/public/events", status_code=201)
async def public_events_compat(
    request: Request,
    body: ScanEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """PRD 兼容路径：POST /api/v1/public/events"""
    return await report_scan_event(request, body, db)


# ---- /api/v1/public/leads → 复用 consumers/lead-capture 逻辑 ----

from app.api.v1.consumers import (
    LeadCaptureRequest,
    VerifiedConsumerScanContext,
    lead_capture,
    verify_consumer_scan_request,
)


@prd_compat_router.post("/api/v1/public/leads", status_code=201)
async def public_leads_compat(
    request: Request,
    body: LeadCaptureRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """PRD 兼容路径：POST /api/v1/public/leads"""
    return await lead_capture(request, body, scan_context, db)


# ---- /api/v1/public/benefits/{benefit_id}/claim → 复用 benefit-claims 逻辑 ----

from app.api.v1.benefit_claims import BenefitClaimRequest, claim_benefit_h5
from app.schemas.benefit_claim import PublicBenefitClaimRequest


@prd_compat_router.post("/api/v1/public/benefits/{benefit_id}/claim", status_code=201)
async def public_benefit_claim_compat(
    benefit_id: uuid.UUID,
    request: Request,
    body: PublicBenefitClaimRequest | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """PRD 兼容路径：POST /api/v1/public/benefits/{benefit_id}/claim"""
    if body is not None and body.benefit_id is not None and body.benefit_id != benefit_id:
        raise HTTPException(status_code=422, detail="benefit_id in path and body must match")
    canonical_body = BenefitClaimRequest(
        benefit_id=benefit_id,
        scan_token=body.scan_token if body is not None else None,
        phone=body.phone if body is not None else None,
    )
    return await claim_benefit_h5(request, canonical_body, db)


# ---- /api/v1/risk/rules → 复用 risk-rules 逻辑 ----

from fastapi import Header

from app.api.v1.risk_rules import (
    create_risk_rule_endpoint,
    list_risk_rules_endpoint,
    update_risk_rule_endpoint,
)
from app.core.dependencies import get_current_tenant, require_tenant_feature
from app.schemas.risk_rule import RiskRuleCreate, RiskRuleUpdate
from app.services.risk_access import risk_dependencies

_require_risk_feature = require_tenant_feature("risk_module", db_scope="function")


@prd_compat_router.get("/api/v1/risk/rules", dependencies=risk_dependencies("risk:read"))
async def list_risk_rules_compat(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _feature: None = Depends(_require_risk_feature),
):
    """PRD 兼容路径：GET /api/v1/risk/rules"""
    return await list_risk_rules_endpoint(db, tenant_id)


@prd_compat_router.post("/api/v1/risk/rules", status_code=201, dependencies=risk_dependencies("risk:manage"))
async def create_risk_rule_compat(
    body: RiskRuleCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _feature: None = Depends(_require_risk_feature),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    """PRD 兼容路径：POST /api/v1/risk/rules"""
    return await create_risk_rule_endpoint(body, db, tenant_id, idempotency_key)


@prd_compat_router.patch("/api/v1/risk/rules/{rule_id}", dependencies=risk_dependencies("risk:manage"))
async def update_risk_rule_compat(
    rule_id: uuid.UUID,
    body: RiskRuleUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _feature: None = Depends(_require_risk_feature),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    """PRD 兼容路径：PATCH /api/v1/risk/rules/{rule_id}"""
    return await update_risk_rule_endpoint(rule_id, body, db, tenant_id, idempotency_key)


# ---- /api/v1/analytics/business-dashboard → 复用 dashboard 逻辑 ----

from app.services.analytics import get_dashboard


@prd_compat_router.get("/api/v1/analytics/business-dashboard")
async def business_dashboard_compat(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    """PRD 兼容路径：GET /api/v1/analytics/business-dashboard"""
    return await get_dashboard(db, tenant_id)
