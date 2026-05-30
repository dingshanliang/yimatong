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
        logger.error("WeChat OAuth failed: errcode=%s", token_data.get("errcode", "unknown"))
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
    from app.services.redpacket import claim_red_packet

    try:
        result = await claim_red_packet(
            db=db,
            benefit_id=benefit_id,
            tenant_id=benefit.tenant_id,
            connector_id=connector.id,
            consumer_id=str(consumer.id),
            openid=openid,
            total_count=total_count,
        )
    except (RuntimeError, ValueError) as e:
        status_code = 400 if isinstance(e, ValueError) else 410
        raise HTTPException(status_code=status_code, detail=str(e))

    amount = result["amount"]
    claim_id = result["claim_id"]
    delivery_status = result["status"]

    await db.commit()

    # 重定向回 H5 结果页
    h5_base = getattr(settings, "h5_base_url", "http://localhost:3001")
    from fastapi.responses import RedirectResponse

    redirect_url = (
        f"{h5_base}/redpacket/result"
        f"?amount={amount}"
        f"&status={delivery_status}"
        f"&claim_id={claim_id}"
    )
    return RedirectResponse(url=redirect_url)
