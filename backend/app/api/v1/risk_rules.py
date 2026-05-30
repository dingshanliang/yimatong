"""风控规则引擎 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.risk_rule import (
    attach_rule_to_campaign,
    create_risk_rule,
    delete_risk_rule,
    evaluate_campaign_rules,
    evaluate_rule,
    get_risk_rule,
    list_interceptions,
    list_risk_rules,
    update_risk_rule,
)

risk_rule_router = APIRouter(prefix="/api/v1/risk-rules", tags=["risk-rules"])


class RiskRuleCreate(BaseModel):
    name: str
    rule_type: str
    action: str
    config: dict


class RiskRuleUpdate(BaseModel):
    name: str | None = None
    action: str | None = None
    config: dict | None = None
    enabled: bool | None = None


class EvaluateRequest(BaseModel):
    rule_type: str
    context: dict
    consumer_id: str | None = None


class CampaignEvaluateRequest(BaseModel):
    context: dict
    consumer_id: str | None = None


# --- 静态路径必须放在 /{rule_id} 之前 ---


@risk_rule_router.post("", status_code=201, summary="创建 风控规则")
async def create_risk_rule_endpoint(
    body: RiskRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await create_risk_rule(
        db,
        tenant_id,
        body.name,
        body.rule_type,
        body.action,
        body.config,
    )
    return {
        "id": str(rule.id),
        "name": rule.name,
        "rule_type": rule.rule_type,
        "action": rule.action,
        "config": rule.config,
        "enabled": rule.enabled,
    }


@risk_rule_router.get("", summary="风控规则 列表")
async def list_risk_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await list_risk_rules(db, tenant_id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "rule_type": r.rule_type,
            "action": r.action,
            "config": r.config,
            "enabled": r.enabled,
        }
        for r in rules
    ]


@risk_rule_router.post("/evaluate")
async def evaluate_rule_endpoint(
    body: EvaluateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await evaluate_rule(db, tenant_id, body.rule_type, body.context, body.consumer_id)
    return result


@risk_rule_router.post("/evaluate/campaign/{campaign_id}")
async def evaluate_campaign_endpoint(
    campaign_id: uuid.UUID,
    body: CampaignEvaluateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await evaluate_campaign_rules(db, tenant_id, campaign_id, body.context)
    return result


@risk_rule_router.get("/interceptions", summary="interceptions 列表")
async def list_interceptions_endpoint(
    action: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    records, total = await list_interceptions(db, tenant_id, action=action, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(r.id),
                "risk_rule_id": str(r.risk_rule_id),
                "campaign_id": str(r.campaign_id) if r.campaign_id else None,
                "action": r.action,
                "context": r.context,
                "consumer_id": r.consumer_id,
            }
            for r in records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- 动态路径放在最后 ---


@risk_rule_router.get("/{rule_id}", summary="获取 风控规则")
async def get_risk_rule_endpoint(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await get_risk_rule(db, tenant_id, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Risk rule not found")
    return {
        "id": str(rule.id),
        "name": rule.name,
        "rule_type": rule.rule_type,
        "action": rule.action,
        "config": rule.config,
        "enabled": rule.enabled,
    }


@risk_rule_router.patch("/{rule_id}", summary="更新 风控规则")
async def update_risk_rule_endpoint(
    rule_id: uuid.UUID,
    body: RiskRuleUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        rule = await update_risk_rule(db, tenant_id, rule_id, **updates)
    except ValueError:
        raise HTTPException(status_code=404, detail="Risk rule not found")
    return {
        "id": str(rule.id),
        "name": rule.name,
        "rule_type": rule.rule_type,
        "action": rule.action,
        "config": rule.config,
        "enabled": rule.enabled,
    }


@risk_rule_router.delete("/{rule_id}", summary="删除 风控规则")
async def delete_risk_rule_endpoint(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        await delete_risk_rule(db, tenant_id, rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Risk rule not found")
    return {"deleted": True}


@risk_rule_router.post("/{rule_id}/campaigns/{campaign_id}")
async def attach_rule_to_campaign_endpoint(
    rule_id: uuid.UUID,
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    link = await attach_rule_to_campaign(db, tenant_id, rule_id, campaign_id)
    return {"attached": True, "rule_id": str(link.risk_rule_id), "campaign_id": str(link.campaign_id)}
