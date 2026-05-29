"""权益领取端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.redis_cache import RedisCache

benefit_claim_router = APIRouter(prefix="/api/v1", tags=["benefit-claims"])

_claim_cache = RedisCache(prefix="claim", default_ttl=300)


class BenefitClaimRequest(BaseModel):
    benefit_id: str
    scan_token: str | None = None
    phone: str | None = None


@benefit_claim_router.post("/benefit-claims", status_code=201)
async def claim_benefit_h5(
    request: Request,
    body: BenefitClaimRequest,
    db: AsyncSession = Depends(get_db),
):
    """H5 端权益领取（scan_token 鉴权，无需 admin token）"""
    # 1. 验证 scan_token
    auth_header = request.headers.get("Authorization", "")
    token = body.scan_token
    if not token and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="scan_token required")

    # 复用 scan_token 服务层验证（不校验 public_id，仅验证 type 和签名）
    import jwt

    from app.core.config import settings
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        raise HTTPException(status_code=401, detail="invalid token")
    except jwt.exceptions.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")

    if payload.get("type") != "scan_token":
        raise HTTPException(status_code=401, detail="invalid token type")

    # 2. 查找权益
    from app.models.campaign import Benefit
    try:
        benefit_id = uuid.UUID(body.benefit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid benefit_id")

    result = await db.execute(select(Benefit).where(Benefit.id == benefit_id))
    benefit = result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")

    # 3. 检查库存
    if benefit.stock_total <= 0:
        raise HTTPException(status_code=410, detail="权益已抢光")

    # 4. 双层幂等：Redis 缓存层 + DB 唯一约束
    idempotency_key = f"claim:{token[:16]}:{benefit_id}"

    if not _claim_cache.set_idempotent(idempotency_key, ttl=300):
        raise HTTPException(status_code=409, detail="already claimed")

    from app.models.campaign import BenefitClaim
    existing = await db.execute(
        select(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.idempotency_key == idempotency_key,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="already claimed")

    # 5. 如果需要手机号
    if benefit.config_json.get("require_phone") and not body.phone:
        raise HTTPException(
            status_code=403,
            detail={"code": "require_auth", "message": "需要授权手机号"},
        )

    # 6. 创建领取记录
    claim = BenefitClaim(
        tenant_id=benefit.tenant_id,
        benefit_id=benefit_id,
        campaign_id=benefit.campaign_id,
        consumer_id=idempotency_key,
        idempotency_key=idempotency_key,
        status="claimed",
    )
    db.add(claim)

    benefit.stock_total -= 1
    await db.commit()

    return {"status": "claimed", "benefit_id": str(benefit_id)}
