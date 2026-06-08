"""权益领取端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import rate_limiter
from app.schemas.benefit_claim import BenefitClaimRequest
from app.services.redis_cache import AsyncRedisCache
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

benefit_claim_router = APIRouter(prefix="/api/v1", tags=["benefit-claims"])

_claim_cache = AsyncRedisCache(prefix="claim", default_ttl=300)


@benefit_claim_router.post("/benefit-claims", status_code=201)
async def claim_benefit_h5(
    request: Request,
    body: BenefitClaimRequest,
    db: AsyncSession = Depends(get_db),
):
    """H5 端权益领取（scan_token 鉴权，无需 admin token）"""
    # 0. IP 级速率限制
    client_ip = get_client_ip(request)
    rate_result = await rate_limiter.check(f"claim:{client_ip}", 20, 60)
    if not rate_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    # 1. 验证 scan_token
    auth_header = request.headers.get("Authorization", "")
    token = body.scan_token
    if not token and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="scan_token required")

    ip_hash = compute_ip_hash(client_ip)
    payload = verify_scan_token(token, expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid token")

    # 2. 查找权益（带租户隔离：只能领取 scan_token 所属租户的权益）
    from app.models.campaign import Benefit

    benefit_id = body.benefit_id  # Pydantic 已验证为 UUID

    # 从 scan_token payload 中提取 tenant_id，确保只能领取同租户的权益
    token_tenant_id = payload.get("tenant_id")
    if not token_tenant_id:
        raise HTTPException(status_code=401, detail="invalid token: missing tenant_id")
    try:
        tid = uuid.UUID(token_tenant_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid token: corrupt tenant_id")

    result = await db.execute(select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tid))
    benefit = result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")

    # 3. 权益状态检查（对所有类型生效，包括红包）
    if benefit.status != "active":
        raise HTTPException(status_code=409, detail="权益已停用")

    # 4. 红包类权益特殊处理：需要走 OAuth 获取 OpenID
    if benefit.benefit_type == "cash_red_packet":
        return await _handle_cash_red_packet_claim(benefit, token, payload, db)

    # 5. 企业微信添加门槛：只以后端收到的企业微信事件为准
    from app.models.campaign import Campaign
    from app.services.wecom_integration import (
        WeComIntegrationError,
        get_or_create_claim_contact_way,
        has_confirmed_wecom_contact,
        is_wecom_required,
    )

    campaign_result = await db.execute(
        select(Campaign).where(Campaign.id == benefit.campaign_id, Campaign.tenant_id == benefit.tenant_id)
    )
    campaign = campaign_result.scalar_one_or_none()
    if campaign and is_wecom_required(campaign.rules_json):
        if not await has_confirmed_wecom_contact(
            db,
            tenant_id=benefit.tenant_id,
            benefit_id=benefit.id,
            scan_token=token,
        ):
            try:
                contact_way = await get_or_create_claim_contact_way(
                    db,
                    tenant_id=benefit.tenant_id,
                    benefit=benefit,
                    scan_token=token,
                )
                await db.commit()
            except WeComIntegrationError as exc:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "require_wecom_contact", "message": str(exc)},
                ) from exc
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "require_wecom_contact",
                    "message": "请先添加企业微信，再继续领取权益",
                    "qr_code": contact_way.qr_code,
                    "state": contact_way.state,
                },
            )

    # 6. 如果需要手机号，先提示补全，避免提前占用幂等 key
    if benefit.config_json.get("require_phone") and not body.phone:
        raise HTTPException(
            status_code=403,
            detail={"code": "require_auth", "message": "需要授权手机号"},
        )

    # 7. 双层幂等：Redis 缓存层 + DB 唯一约束
    idempotency_key = f"claim:{token[:16]}:{benefit_id}"
    if not await _claim_cache.set_idempotent(idempotency_key, ttl=300):
        raise HTTPException(status_code=409, detail="already claimed")

    from app.services.campaign import claim_benefit

    consumer_id = str(payload.get("consumer_id") or idempotency_key)
    result = await claim_benefit(
        db,
        benefit.tenant_id,
        benefit_id,
        consumer_id,
        idempotency_key,
    )
    if result["status"] in {"idempotent", "success"}:
        await db.commit()
        return {"status": "claimed", "benefit_id": str(benefit_id)}
    if result["status"] == "inactive":
        raise HTTPException(status_code=409, detail="权益已停用")
    if result["status"] == "campaign_inactive":
        raise HTTPException(status_code=409, detail="活动已结束")
    if result["status"] == "out_of_stock":
        raise HTTPException(status_code=410, detail="权益已抢光")
    if result["status"] == "limit_reached":
        raise HTTPException(status_code=403, detail="您已达到本次活动领取上限")
    raise HTTPException(status_code=404, detail="benefit not found")


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

    # 1.2 原子化限额检查：使用 SELECT FOR UPDATE 锁定权益行
    # 确保同一用户的并发领取请求串行化，防止限额绕过
    from app.models.campaign import Benefit as BenefitModel

    # 锁定权益行，防止并发读取到相同的 claimed_count
    locked_benefit = await db.execute(select(BenefitModel).where(BenefitModel.id == benefit.id).with_for_update())
    _locked = locked_benefit.scalar_one_or_none()

    total_count_result = await db.execute(
        select(func.count())
        .select_from(BenefitClaim)
        .where(
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
        select(func.count())
        .select_from(BenefitClaim)
        .where(
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
