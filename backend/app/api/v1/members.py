"""会员与积分 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.member import (
    award_points,
    create_point_rule,
    get_consumer_profile,
    get_or_create_consumer,
    get_point_rules,
    list_point_transactions,
    spend_points,
)

member_router = APIRouter(prefix="/api/v1/members", tags=["members"])


class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = None


class AwardPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int
    reason: str
    reference_id: str | None = None


class SpendPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int
    reason: str
    reference_id: str | None = None


class PointRuleCreate(BaseModel):
    rule_type: str
    points: int


@member_router.post("/consumers", status_code=201)
async def create_consumer_endpoint(
    body: ConsumerCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    consumer = await get_or_create_consumer(db, tenant_id, phone=body.phone)
    return {
        "id": str(consumer.id),
        "member_level": consumer.member_level,
        "total_points": consumer.total_points,
    }


@member_router.get("/consumers/{consumer_id}")
async def get_consumer_endpoint(
    consumer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    profile = await get_consumer_profile(db, tenant_id, consumer_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Consumer not found")
    return profile


@member_router.post("/points/award")
async def award_points_endpoint(
    body: AwardPointsRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        txn = await award_points(
            db,
            tenant_id,
            body.consumer_id,
            body.points,
            body.reason,
            body.reference_id,
        )
        return {
            "id": str(txn.id),
            "amount": txn.amount,
            "balance_after": txn.balance_after,
            "txn_type": txn.txn_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@member_router.post("/points/spend")
async def spend_points_endpoint(
    body: SpendPointsRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        txn = await spend_points(
            db,
            tenant_id,
            body.consumer_id,
            body.points,
            body.reason,
            body.reference_id,
        )
        return {
            "id": str(txn.id),
            "amount": txn.amount,
            "balance_after": txn.balance_after,
            "txn_type": txn.txn_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@member_router.get("/consumers/{consumer_id}/transactions")
async def list_transactions_endpoint(
    consumer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    txns, total = await list_point_transactions(
        db,
        tenant_id,
        consumer_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
            }
            for t in txns
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@member_router.get("/point-rules")
async def list_point_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await get_point_rules(db, tenant_id)
    return [{"id": str(r.id), "rule_type": r.rule_type, "points": r.points} for r in rules]


@member_router.post("/point-rules", status_code=201)
async def create_point_rule_endpoint(
    body: PointRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await create_point_rule(db, tenant_id, body.rule_type, body.points)
    return {"id": str(rule.id), "rule_type": rule.rule_type, "points": rule.points}
