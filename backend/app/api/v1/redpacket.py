"""现金红包 API"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.services.redpacket import (
    check_risk,
    claim_redpacket,
    create_rule,
    get_kyc_status,
    list_rules,
    list_withdrawals,
    request_withdrawal,
    submit_kyc,
)

redpacket_router = APIRouter(prefix="/api/v1/redpacket", tags=["redpacket"])


class RuleCreate(BaseModel):
    name: str
    total_budget: int
    min_amount: int
    max_amount: int
    daily_limit_per_user: int = 3
    single_limit_per_user: int = 10
    require_kyc: bool = False
    start_time: datetime
    end_time: datetime


class KYCSubmit(BaseModel):
    real_name: str
    id_number: str
    phone: str


class ClaimRequest(BaseModel):
    rule_id: str


class WithdrawalRequest(BaseModel):
    amount: int


class RiskCheckRequest(BaseModel):
    rule_id: str
    amount: int


@redpacket_router.post("/rules", status_code=201)
async def create_rule_endpoint(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await create_rule(
        db,
        tenant_id,
        name=body.name,
        total_budget=body.total_budget,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
        daily_limit_per_user=body.daily_limit_per_user,
        single_limit_per_user=body.single_limit_per_user,
        require_kyc=body.require_kyc,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    return {
        "id": str(rule.id),
        "name": rule.name,
        "total_budget": rule.total_budget,
        "min_amount": rule.min_amount,
        "max_amount": rule.max_amount,
        "daily_limit_per_user": rule.daily_limit_per_user,
        "single_limit_per_user": rule.single_limit_per_user,
        "require_kyc": rule.require_kyc,
        "start_time": rule.start_time.isoformat(),
        "end_time": rule.end_time.isoformat(),
        "status": rule.status,
    }


@redpacket_router.get("/rules")
async def list_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await list_rules(db, tenant_id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "total_budget": r.total_budget,
            "min_amount": r.min_amount,
            "max_amount": r.max_amount,
            "status": r.status,
        }
        for r in rules
    ]


@redpacket_router.post("/kyc", status_code=201)
async def submit_kyc_endpoint(
    body: KYCSubmit,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    kyc = await submit_kyc(db, tenant_id, account_id, body.real_name, body.id_number, body.phone)
    return {
        "id": str(kyc.id),
        "real_name": kyc.real_name,
        "status": kyc.status,
    }


@redpacket_router.get("/kyc/status")
async def get_kyc_status_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    kyc = await get_kyc_status(db, tenant_id, account_id)
    if not kyc:
        return {"status": "none"}
    return {"status": kyc.status}


@redpacket_router.post("/claim")
async def claim_endpoint(
    body: ClaimRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    try:
        claim = await claim_redpacket(db, tenant_id, account_id, uuid.UUID(body.rule_id))
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {
        "id": str(claim.id),
        "amount": claim.amount,
        "status": claim.status,
    }


@redpacket_router.post("/withdrawals", status_code=201)
async def request_withdrawal_endpoint(
    body: WithdrawalRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    w = await request_withdrawal(db, tenant_id, account_id, body.amount)
    return {
        "id": str(w.id),
        "amount": w.amount,
        "status": w.status,
    }


@redpacket_router.get("/withdrawals")
async def list_withdrawals_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    withdrawals = await list_withdrawals(db, tenant_id)
    return [{"id": str(w.id), "amount": w.amount, "status": w.status} for w in withdrawals]


@redpacket_router.post("/risk-check")
async def risk_check_endpoint(
    body: RiskCheckRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return check_risk(body.rule_id, body.amount)
