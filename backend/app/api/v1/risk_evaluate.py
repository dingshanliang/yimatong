"""风控实时评估 API"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant, require_tenant_feature
from app.models.scan import ScanEvent
from app.services.risk_access import risk_dependencies

risk_evaluate_router = APIRouter(
    prefix="/api/v1/risk",
    tags=["risk-evaluate"],
    dependencies=[Depends(require_tenant_feature("risk_module"))],
)


class RiskEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    public_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class RiskEvaluateResponse(BaseModel):
    risk_level: str
    risk_factors: list[str]
    scan_count: int


@risk_evaluate_router.post(
    "/evaluate", response_model=RiskEvaluateResponse, dependencies=risk_dependencies("risk:evaluate")
)
async def evaluate_risk(
    body: RiskEvaluateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """实时风险评估"""
    risk_factors = []
    risk_score = 0

    # 扫码频率检查
    scan_count_stmt = (
        select(func.count())
        .select_from(ScanEvent)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id == body.public_id,
        )
    )
    result = await db.execute(scan_count_stmt)
    scan_count = result.scalar() or 0

    if scan_count > 50:
        risk_factors.append("high_scan_frequency")
        risk_score += 3
    elif scan_count > 20:
        risk_factors.append("medium_scan_frequency")
        risk_score += 1

    ip_count_stmt = select(func.count(func.distinct(ScanEvent.ip_hash))).where(
        ScanEvent.tenant_id == tenant_id,
        ScanEvent.public_id == body.public_id,
    )
    ip_count = (await db.execute(ip_count_stmt)).scalar() or 0
    if ip_count > 5:
        risk_factors.append("multiple_locations")
        risk_score += 2

    # 确定风险等级
    if risk_score >= 3:
        risk_level = "high"
    elif risk_score >= 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return RiskEvaluateResponse(
        risk_level=risk_level,
        risk_factors=risk_factors,
        scan_count=scan_count,
    )
