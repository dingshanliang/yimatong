"""Tenant risk-rule APIs backed by database authority functions."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import get_db
from app.core.dependencies import get_current_tenant, require_tenant_feature
from app.schemas.common import PaginatedResponse
from app.schemas.risk_rule import ExactScanRiskEvaluate, RiskPauseResume, RiskRuleCreate, RiskRuleUpdate
from app.services.risk_access import risk_dependencies
from app.services.risk_authority import evaluate_execute_scan, mutate_rule, resume_campaign_pause, set_campaign_rule
from app.services.risk_rule import (
    get_risk_rule,
    list_campaign_pauses,
    list_interceptions,
    list_risk_rules,
    normalize_rule_config,
)

risk_rule_router = APIRouter(
    prefix="/api/v1/risk-rules",
    tags=["risk-rules"],
    dependencies=[Depends(require_tenant_feature("risk_module", db_scope="function"))],
)


def _rule_payload(rule) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "rule_type": rule.rule_type,
        "action": rule.action,
        "config": rule.config,
        "enabled": rule.enabled,
        "version": getattr(rule, "version", 1),
    }


async def _reload_rule(db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID):
    rule = await get_risk_rule(db, tenant_id, rule_id, refresh=True)
    if rule is None:
        raise HTTPException(status_code=404, detail="Risk rule not found")
    return rule


@risk_rule_router.post("", status_code=201, summary="创建风控规则", dependencies=risk_dependencies("risk:manage"))
async def create_risk_rule_endpoint(
    body: RiskRuleCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    rule_id = uuid7()
    result = await mutate_rule(
        db,
        tenant_id,
        action="create",
        rule_id=rule_id,
        expected_version=None,
        idempotency_key=idempotency_key,
        name=body.name,
        rule_type=body.rule_type,
        rule_action=body.action,
        config=body.config,
        enabled=True,
    )
    rule = await _reload_rule(db, tenant_id, result["rule_id"])
    return {**_rule_payload(rule), "replayed": result["replayed"]}


@risk_rule_router.get("", summary="风控规则列表", dependencies=risk_dependencies("risk:read"))
async def list_risk_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return [_rule_payload(rule) for rule in await list_risk_rules(db, tenant_id)]


@risk_rule_router.post(
    "/evaluate/scan", summary="按持久化扫码事实评估规则", dependencies=risk_dependencies("risk:evaluate")
)
async def evaluate_scan_endpoint(
    body: ExactScanRiskEvaluate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    return await evaluate_execute_scan(
        db,
        tenant_id,
        scan_event_id=body.scan_event_id,
        rule_id=body.rule_id,
        receipt_id=uuid.uuid5(tenant_id, f"risk-evaluate:{idempotency_key}"),
        idempotency_key=idempotency_key,
        context={},
    )


@risk_rule_router.get("/pauses", summary="风控活动暂停列表", dependencies=risk_dependencies("risk:read"))
async def list_campaign_pauses_endpoint(
    status: str | None = Query(None, pattern="^(active|resumed)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    pauses, total = await list_campaign_pauses(
        db, tenant_id, status=status, page=page, page_size=page_size
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(pause.id),
                "receipt_id": str(pause.receipt_id),
                "campaign_id": str(pause.campaign_id),
                "risk_rule_id": str(pause.risk_rule_id),
                "prior_status": pause.prior_status,
                "status": pause.status,
                "version": pause.version,
                "paused_at": pause.paused_at,
                "resumed_at": pause.resumed_at,
                "resume_reason": pause.resume_reason,
            }
            for pause in pauses
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@risk_rule_router.post(
    "/pauses/{pause_id}/resume", summary="恢复风控暂停活动", dependencies=risk_dependencies("risk:manage")
)
async def resume_campaign_pause_endpoint(
    pause_id: uuid.UUID,
    body: RiskPauseResume,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    return await resume_campaign_pause(
        db,
        tenant_id,
        pause_id=pause_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        reason=body.reason,
    )


@risk_rule_router.get("/interceptions", summary="拦截记录列表", dependencies=risk_dependencies("risk:read"))
async def list_interceptions_endpoint(
    action: str | None = Query(None, pattern="^(block|warn)$"),
    auto_triggered: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    records, total = await list_interceptions(
        db, tenant_id, action=action, auto_triggered=auto_triggered, page=page, page_size=page_size
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(record.id),
                "risk_rule_id": str(record.risk_rule_id),
                "campaign_id": str(record.campaign_id) if record.campaign_id else None,
                "action": record.action,
                "context": record.context,
                "consumer_id": record.consumer_id,
                "auto_triggered": record.auto_triggered,
                "action_taken": record.action_taken,
                "action_detail": record.action_detail,
            }
            for record in records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@risk_rule_router.get("/{rule_id}", summary="获取风控规则", dependencies=risk_dependencies("risk:read"))
async def get_risk_rule_endpoint(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return _rule_payload(await _reload_rule(db, tenant_id, rule_id))


@risk_rule_router.patch("/{rule_id}", summary="更新风控规则", dependencies=risk_dependencies("risk:manage"))
async def update_risk_rule_endpoint(
    rule_id: uuid.UUID,
    body: RiskRuleUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    current = await _reload_rule(db, tenant_id, rule_id)
    config = normalize_rule_config(current.rule_type, body.config) if body.config is not None else None
    result = await mutate_rule(
        db,
        tenant_id,
        action="update",
        rule_id=rule_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        name=body.name if body.name is not None else current.name,
        rule_type=current.rule_type,
        rule_action=body.action if body.action is not None else current.action,
        config=config if config is not None else current.config,
        enabled=body.enabled if body.enabled is not None else current.enabled,
    )
    rule = await _reload_rule(db, tenant_id, result["rule_id"])
    return {**_rule_payload(rule), "replayed": result["replayed"]}


@risk_rule_router.delete("/{rule_id}", summary="删除风控规则", dependencies=risk_dependencies("risk:manage"))
async def delete_risk_rule_endpoint(
    rule_id: uuid.UUID,
    expected_version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    current = await _reload_rule(db, tenant_id, rule_id)
    result = await mutate_rule(
        db,
        tenant_id,
        action="disable",
        rule_id=rule_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        name=current.name,
        rule_type=current.rule_type,
        rule_action=current.action,
        config=current.config,
        enabled=False,
    )
    return {"deleted": False, "disabled": True, **result}


@risk_rule_router.post(
    "/{rule_id}/campaigns/{campaign_id}", summary="关联活动规则", dependencies=risk_dependencies("risk:manage")
)
async def attach_rule_to_campaign_endpoint(
    rule_id: uuid.UUID,
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    return await set_campaign_rule(
        db,
        tenant_id,
        rule_id=rule_id,
        campaign_id=campaign_id,
        attach=True,
        idempotency_key=idempotency_key,
    )


@risk_rule_router.delete(
    "/{rule_id}/campaigns/{campaign_id}", summary="解除活动规则", dependencies=risk_dependencies("risk:manage")
)
async def detach_rule_from_campaign_endpoint(
    rule_id: uuid.UUID,
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
):
    return await set_campaign_rule(
        db,
        tenant_id,
        rule_id=rule_id,
        campaign_id=campaign_id,
        attach=False,
        idempotency_key=idempotency_key,
    )
