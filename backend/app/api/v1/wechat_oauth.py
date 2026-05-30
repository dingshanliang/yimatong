"""微信 OAuth 回调与红包领取端点。

处理流程：
1. H5 前端检测到红包权益 → 调 /api/v1/wechat/auth-url 获取 OAuth 跳转地址
2. 前端跳转微信 OAuth（snsapi_base 静默授权）
3. 微信回调 → /api/v1/wechat/oauth-callback → 换 OpenID → 创建/查找 ConsumerProfile → 自动完成红包领取
4. 重定向回 H5 结果页
"""

from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


def _get_connector_config(connector) -> dict:
    """解密并合并 connector 配置。"""
    from app.services.connectors.secrets import decrypt_secrets

    cfg = connector.config.copy() if connector.config else {}
    if connector.secrets_encrypted:
        secrets = decrypt_secrets(connector.secrets_encrypted)
        cfg.update(secrets)
    return cfg

wechat_oauth_router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-oauth"])

OAUTH_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"


class AuthUrlRequest(BaseModel):
    benefit_id: str
    scan_token: str


@wechat_oauth_router.get("/auth-url")
async def get_auth_url(
    benefit_id: str,
    scan_token: str,
    db: AsyncSession = Depends(get_db),
):
    """生成微信 OAuth URL，state 编码 benefit_id + scan_token。"""
    from app.models.campaign import Benefit

    try:
        bid = uuid.UUID(benefit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid benefit_id")

    result = await db.execute(select(Benefit).where(Benefit.id == bid))
    benefit = result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")

    if benefit.benefit_type != "cash_red_packet":
        raise HTTPException(status_code=400, detail="not a cash red packet benefit")

    # 查找租户的 wechat_pay_transfer connector
    from app.models.connector import Connector

    conn_result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == benefit.tenant_id,
            Connector.connector_type == "wechat_pay_transfer",
            Connector.enabled.is_(True),
        )
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=422, detail="No wechat_pay_transfer connector configured for this tenant")

    # 从 connector 获取 OA appid
    cfg = _get_connector_config(connector)

    oa_appid = cfg.get("oa_appid")
    if not oa_appid:
        raise HTTPException(status_code=422, detail="Connector missing oa_appid")

    # state 编码：base64(benefit_id:scan_token)
    import base64
    from urllib.parse import quote

    state_str = f"{benefit_id}:{scan_token}"
    state = base64.urlsafe_b64encode(state_str.encode()).decode()

    # 回调地址
    callback_url = quote(f"{settings.base_url}/api/v1/wechat/oauth-callback", safe="")

    auth_url = (
        f"{OAUTH_URL}?appid={oa_appid}"
        f"&redirect_uri={callback_url}"
        f"&response_type=code"
        f"&scope=snsapi_base"
        f"&state={state}#wechat_redirect"
    )

    return {"auth_url": auth_url}


@wechat_oauth_router.get("/oauth-callback")
async def oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """微信 OAuth 回调：换 OpenID → 创建 ConsumerProfile → 自动领取红包。"""
    import base64

    # 解码 state
    try:
        state_str = base64.urlsafe_b64decode(state.encode()).decode()
        benefit_id_str, scan_token = state_str.split(":", 1)
        benefit_id = uuid.UUID(benefit_id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid state parameter")

    # 查找 benefit
    from app.models.campaign import Benefit

    benefit_result = await db.execute(select(Benefit).where(Benefit.id == benefit_id))
    benefit = benefit_result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")

    # 查找 connector
    from app.models.connector import Connector

    conn_result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == benefit.tenant_id,
            Connector.connector_type == "wechat_pay_transfer",
            Connector.enabled.is_(True),
        )
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=422, detail="No wechat_pay_transfer connector configured")

    cfg = _get_connector_config(connector)

    oa_appid = cfg.get("oa_appid")
    oa_appsecret = cfg.get("oa_appsecret")

    if not oa_appid or not oa_appsecret:
        raise HTTPException(status_code=422, detail="Connector missing oa_appid or oa_appsecret")

    # 用 code 换 access_token + openid
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.get(
            TOKEN_URL,
            params={
                "appid": oa_appid,
                "secret": oa_appsecret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )

    token_data = token_resp.json()
    openid = token_data.get("openid")
    if not openid:
        logger.error("WeChat OAuth failed: %s", token_data)
        raise HTTPException(status_code=401, detail="Failed to get OpenID from WeChat")

    # 创建/查找 ConsumerProfile
    from sqlalchemy.exc import IntegrityError

    from app.models.member import ConsumerProfile

    existing = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == benefit.tenant_id,
            ConsumerProfile.wechat_openid == openid,
        )
    )
    consumer = existing.scalar_one_or_none()
    if not consumer:
        consumer = ConsumerProfile(
            tenant_id=benefit.tenant_id,
            wechat_openid=openid,
        )
        db.add(consumer)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await db.execute(
                select(ConsumerProfile).where(
                    ConsumerProfile.tenant_id == benefit.tenant_id,
                    ConsumerProfile.wechat_openid == openid,
                )
            )
            consumer = existing.scalar_one_or_none()
            if not consumer:
                raise

    # 检查每用户领取次数限制
    from datetime import UTC, datetime

    from sqlalchemy import func

    from app.models.campaign import BenefitClaim

    rp_config = benefit.config_json
    daily_limit = rp_config.get("daily_limit_per_user", 3)
    total_limit = rp_config.get("total_limit_per_user", 10)

    total_count_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.consumer_id == str(consumer.id),
        )
    )
    total_count = total_count_result.scalar() or 0
    if total_count >= total_limit:
        raise HTTPException(status_code=429, detail="已达到总领取上限")

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.consumer_id == str(consumer.id),
            BenefitClaim.created_at >= today_start,
        )
    )
    daily_count = daily_count_result.scalar() or 0
    if daily_count >= daily_limit:
        raise HTTPException(status_code=429, detail="已达到今日领取上限")

    # 自动执行红包领取
    from app.services.redpacket_amount import calc_amount, validate_config

    # FOR UPDATE 读取最新 stock_total 和 config_json（避免陈旧读）
    from app.models.campaign import Benefit as BenefitModel

    fresh = await db.execute(
        select(BenefitModel).where(BenefitModel.id == benefit_id).with_for_update()
    )
    benefit_fresh = fresh.scalar_one_or_none()
    if not benefit_fresh or benefit_fresh.stock_total <= 0:
        raise HTTPException(status_code=410, detail="红包已抢光")

    config = benefit_fresh.config_json
    valid, msg = validate_config(config)
    if not valid:
        raise HTTPException(status_code=400, detail=f"Invalid red packet config: {msg}")

    # 计算金额
    claimed_budget = config.get("claimed_budget", 0)
    budget = config.get("budget", 0)
    remaining = budget - claimed_budget

    if remaining <= 0:
        raise HTTPException(status_code=410, detail="红包预算已用尽")

    if config.get("amount_type") == "lucky":
        remaining_count = benefit_fresh.stock_total
        amount = calc_amount(config, remaining_budget=remaining, remaining_count=remaining_count)
    else:
        amount = calc_amount(config)

    if amount > remaining:
        amount = remaining

    # 乐观锁扣减预算
    from sqlalchemy import text

    result = await db.execute(
        text("""
            UPDATE benefits
            SET config_json = jsonb_set(
                config_json, '{claimed_budget}',
                (COALESCE((config_json->>'claimed_budget')::int, 0) + :amount)::text::jsonb
            ),
            stock_total = GREATEST(stock_total - 1, 0)
            WHERE id = :benefit_id
            AND (COALESCE((config_json->>'claimed_budget')::int, 0) + :amount) <= (config_json->>'budget')::int
        """),
        {"benefit_id": benefit_id, "amount": amount},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=410, detail="红包已抢光")

    # 创建 BenefitClaim
    from app.models.campaign import BenefitClaim

    idempotency_key = f"rp:{consumer.id}:{benefit_id}:{total_count}"
    claim = BenefitClaim(
        tenant_id=benefit.tenant_id,
        benefit_id=benefit_id,
        campaign_id=benefit.campaign_id,
        consumer_id=str(consumer.id),
        idempotency_key=idempotency_key,
        status="claimed",
    )
    db.add(claim)

    # 触发微信转账（通过 BenefitDelivery）
    from app.models.connector import BenefitDelivery

    delivery = BenefitDelivery(
        tenant_id=benefit.tenant_id,
        connector_id=connector.id,
        consumer_id=str(consumer.id),
        benefit_type="cash_red_packet",
        benefit_config={
            "amount": amount,
            "openid": openid,
            "out_bill_no": str(claim.id),
            "transfer_remark": config.get("transfer_remark", "扫码领红包"),
        },
        status="pending",
    )
    db.add(delivery)
    await db.flush()

    # 执行转账
    from app.services.connectors.registry import get_adapter

    adapter = get_adapter(connector)
    delivery_result = await adapter.deliver(connector, str(consumer.id), delivery.benefit_config)

    delivery.status = delivery_result.status
    delivery.external_data = delivery_result.external_data
    if delivery_result.status == "success":
        claim.status = "delivered"

    await db.commit()

    # 重定向回 H5 结果页
    h5_base = getattr(settings, "h5_base_url", "http://localhost:3001")
    from fastapi.responses import RedirectResponse

    redirect_url = (
        f"{h5_base}/redpacket/result"
        f"?amount={amount}"
        f"&status={delivery_result.status}"
        f"&claim_id={claim.id}"
    )
    return RedirectResponse(url=redirect_url)
