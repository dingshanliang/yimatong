"""权益领取端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.redis_cache import AsyncRedisCache

benefit_claim_router = APIRouter(prefix="/api/v1", tags=["benefit-claims"])

_claim_cache = AsyncRedisCache(prefix="claim", default_ttl=300)


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

    # 3. 红包类权益特殊处理：需要走 OAuth 获取 OpenID
    if benefit.benefit_type == "cash_red_packet":
        return await _handle_cash_red_packet_claim(benefit, token, payload, db)

    # 4. 检查库存
    if benefit.stock_total <= 0:
        raise HTTPException(status_code=410, detail="权益已抢光")

    # 5. 双层幂等：Redis 缓存层 + DB 唯一约束
    idempotency_key = f"claim:{token[:16]}:{benefit_id}"

    if not await _claim_cache.set_idempotent(idempotency_key, ttl=300):
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

    # 6. 如果需要手机号
    if benefit.config_json.get("require_phone") and not body.phone:
        raise HTTPException(
            status_code=403,
            detail={"code": "require_auth", "message": "需要授权手机号"},
        )

    # 7. 创建领取记录
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


async def _handle_cash_red_packet_claim(
    benefit,
    token: str,
    payload: dict,
    db: AsyncSession,
):
    """处理现金红包领取。

    如果消费者已有 OpenID（ConsumerProfile），直接领取。
    否则返回 403 + OAuth URL，前端跳转授权。
    """
    tenant_id = benefit.tenant_id

    # 检查租户是否开通了红包功能
    from app.models.tenant import Tenant

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant or not (tenant.enabled_features or {}).get("cash_red_packet"):
        raise HTTPException(status_code=403, detail="cash_red_packet not enabled for this tenant")

    # 检查租户是否配置了微信支付 connector
    from app.models.connector import Connector

    conn_result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == "wechat_pay_transfer",
            Connector.enabled.is_(True),
        )
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=422, detail="No wechat_pay_transfer connector configured")

    # 尝试从 scan_token payload 中获取 consumer_id
    consumer_id_str = payload.get("consumer_id")
    from app.models.member import ConsumerProfile

    consumer = None
    if consumer_id_str:
        try:
            consumer_result = await db.execute(
                select(ConsumerProfile).where(ConsumerProfile.id == uuid.UUID(consumer_id_str))
            )
            consumer = consumer_result.scalar_one_or_none()
        except ValueError:
            pass

    if not consumer or not consumer.wechat_openid:
        # 没有 OpenID，返回需要 OAuth 的信号
        return {
            "status": "require_wechat_auth",
            "benefit_id": str(benefit.id),
            "auth_url_path": f"/api/v1/wechat/auth-url?benefit_id={benefit.id}&scan_token={token}",
        }

    # 有 OpenID，直接执行红包领取
    # 检查限领（在调用 claim_red_packet 前完成）
    from sqlalchemy import func

    from app.models.campaign import BenefitClaim
    from app.services.redpacket import claim_red_packet

    rp_config = benefit.config_json
    daily_limit = rp_config.get("daily_limit_per_user", 3)
    total_limit = rp_config.get("total_limit_per_user", 10)

    total_count_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit.id,
            BenefitClaim.consumer_id == str(consumer.id),
        )
    )
    total_count = total_count_result.scalar() or 0
    if total_count >= total_limit:
        raise HTTPException(status_code=429, detail="已达到总领取上限")

    from datetime import UTC, datetime

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit.id,
            BenefitClaim.consumer_id == str(consumer.id),
            BenefitClaim.created_at >= today_start,
        )
    )
    daily_count = daily_count_result.scalar() or 0
    if daily_count >= daily_limit:
        raise HTTPException(status_code=429, detail="已达到今日领取上限")

    try:
        result = await claim_red_packet(
            db=db,
            benefit_id=benefit.id,
            tenant_id=tenant_id,
            connector_id=connector.id,
            consumer_id=str(consumer.id),
            openid=consumer.wechat_openid,
            total_count=total_count,
        )
    except (RuntimeError, ValueError) as e:
        status_code = 400 if isinstance(e, ValueError) else 410
        raise HTTPException(status_code=status_code, detail=str(e))

    await db.commit()

    return {
        "status": result["status"],
        "benefit_id": str(benefit.id),
        "amount": result["amount"],
        "claim_id": str(result["claim_id"]),
    }
