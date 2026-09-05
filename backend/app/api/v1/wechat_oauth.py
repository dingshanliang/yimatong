"""WeChat OAuth entrypoint for consumer cash-benefit identity binding."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_for_consumer, set_session_tenant_context
from app.models.campaign import Benefit
from app.models.connector import Connector
from app.services.connectors.secrets import decrypt_secrets
from app.services.consent import (
    get_consumer_consent_receipt_status,
    get_current_consumer_policy,
    grant_consumer_consent_authority,
    require_consumer_scan_authority,
)
from app.services.redis_cache import get_redis_pool
from app.services.scan_token import bind_scan_token_consumer, verify_scan_token
from app.services.visitor import link_visitor_to_consumer
from app.services.wechat_oauth_authority import bind_wechat_oauth_consumer_authority
from app.utils.client_ip import compute_ip_hash, get_client_ip

wechat_oauth_router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-oauth"])

_AUTHORIZE_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
_STATE_TTL_SECONDS = 300
_CALLBACK_LOCK_SECONDS = 30


class WeChatOAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benefit_id: uuid.UUID
    scan_token: str = Field(min_length=1, max_length=4096)
    consent_granted: Literal[True]


def _state_key(state: str) -> str:
    return f"wechat-oauth:v1:{state}"


def _authority_digest(label: str, value: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        f"wechat-oauth:{label}\0{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _pending_key(token_jti: str) -> str:
    return f"wechat-oauth:pending:{_authority_digest('token', token_jti)}"


def _rate_key(kind: str, value: str) -> str:
    return f"wechat-oauth:rate:{kind}:{_authority_digest(kind, value)}"


_START_STATE_SCRIPT = """
local existing = redis.call('get', KEYS[1])
if existing then return {1, existing} end
local count = redis.call('incr', KEYS[2])
if count == 1 then redis.call('expire', KEYS[2], ARGV[1]) end
if count > tonumber(ARGV[2]) then return {2, ''} end
redis.call('set', KEYS[1], ARGV[3], 'EX', ARGV[1])
return {0, ARGV[3]}
"""


async def _cash_connector(db: AsyncSession, tenant_id: uuid.UUID, benefit_id: uuid.UUID) -> Connector:
    benefit = await db.scalar(
        select(Benefit).where(
            Benefit.id == benefit_id,
            Benefit.tenant_id == tenant_id,
            Benefit.benefit_type == "cash_red_packet",
            Benefit.status == "active",
        )
    )
    if benefit is None:
        raise HTTPException(status_code=404, detail="cash benefit not found")
    connector = await db.scalar(
        select(Connector).where(
            Connector.id == benefit.connector_id,
            Connector.tenant_id == tenant_id,
            Connector.connector_type == "wechat_pay_transfer",
            Connector.enabled.is_(True),
        )
    )
    if connector is None:
        raise HTTPException(status_code=409, detail="cash benefit connector unavailable")
    return connector


def _oauth_credentials(connector: Connector) -> tuple[str, str]:
    config = dict(connector.config or {})
    if connector.secrets_encrypted:
        config.update(decrypt_secrets(connector.secrets_encrypted))
    appid = config.get("oa_appid")
    secret = config.get("oa_appsecret")
    if not isinstance(appid, str) or not appid or not isinstance(secret, str) or not secret:
        raise HTTPException(status_code=409, detail="WeChat OAuth is not configured")
    return appid, secret


@wechat_oauth_router.post("/auth-url")
async def get_auth_url(
    request: Request,
    body: WeChatOAuthStart,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """Create a short-lived, single-use OAuth state bound to the scanned code."""

    payload = verify_scan_token(body.scan_token, expected_ip_hash=compute_ip_hash(get_client_ip(request)))
    try:
        tenant_id = uuid.UUID(str(payload["tenant_id"])) if payload else None
    except (KeyError, TypeError, ValueError):
        tenant_id = None
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="invalid scan credential")
    await set_session_tenant_context(db, tenant_id)
    connector = await _cash_connector(db, tenant_id, body.benefit_id)
    appid, _secret = _oauth_credentials(connector)

    authority = require_consumer_scan_authority(payload)
    redis = await get_redis_pool()
    if redis is None:
        raise HTTPException(status_code=503, detail="OAuth state service unavailable")
    state = secrets.token_hex(32)
    pending_key = _pending_key(authority.rate_subject)
    client_ip = get_client_ip(request)
    script_result = await redis.eval(
        _START_STATE_SCRIPT,
        2,
        pending_key,
        _rate_key("start-ip", client_ip),
        str(_STATE_TTL_SECONDS),
        "10",
        state,
    )
    outcome, selected_state = int(script_result[0]), str(script_result[1])
    if outcome == 2:
        raise HTTPException(status_code=429, detail="too many OAuth attempts")
    state = selected_state
    if outcome == 0:
        try:
            if authority.consumer_id is not None and not await link_visitor_to_consumer(
                db,
                tenant_id,
                authority.visitor_id,
                authority.consumer_id,
            ):
                raise HTTPException(status_code=403, detail="consumer_visitor_subject_denied")
            policy = await get_current_consumer_policy(db, tenant_id, "wechat_cash_payout")
            consent = await grant_consumer_consent_authority(
                db,
                tenant_id=tenant_id,
                purpose="wechat_cash_payout",
                expected_version=policy["policy_version"],
                expected_digest=policy["policy_digest"],
                scan_event_id=authority.scan_event_id,
                scan_time=authority.scan_time,
                public_id=authority.public_id,
                visitor_id=authority.visitor_id,
                token_consumer_id=authority.consumer_id,
                ip_hash=compute_ip_hash(client_ip),
                user_agent=request.headers.get("user-agent", ""),
                idempotency_key=f"wechat-oauth:{state}",
            )
            if consent.get("status") != "granted":
                raise HTTPException(status_code=409, detail="consent_authority_invalid")
            consent_id = uuid.UUID(str(consent["consent_id"]))
            state_payload = json.dumps(
                {
                    "tenant_id": str(tenant_id),
                    "benefit_id": str(body.benefit_id),
                    "scan_token": body.scan_token,
                    "consent_id": str(consent_id),
                    "pending_key": pending_key,
                },
                separators=(",", ":"),
            )
            await redis.setex(_state_key(state), _STATE_TTL_SECONDS, state_payload)
            await db.commit()
        except Exception:
            await db.rollback()
            await redis.delete(pending_key, _state_key(state))
            raise
    elif not await redis.exists(_state_key(state)):
        raise HTTPException(status_code=503, detail="OAuth state is being prepared")
    redirect_uri = f"{settings.base_url.rstrip('/')}/api/v1/wechat/oauth-callback"
    query = urlencode(
        {
            "appid": appid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "snsapi_base",
            "state": state,
        }
    )
    return {"auth_url": f"{_AUTHORIZE_URL}?{query}#wechat_redirect"}


@wechat_oauth_router.get("/oauth-callback")
async def oauth_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=512)],
    state: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """Consume OAuth state, bind the OpenID, and return a member-bound scan credential to H5."""

    # 失败路径必须回到 H5 体验（回跳码页带失败 fragment；无法定位码页时渲染内联提示页），
    # 不能在微信 webview 里裸吐 JSON——拒绝授权/链接过期是常态路径而非异常。
    redis = await get_redis_pool()
    if redis is None:
        return _oauth_failure_html("service_unavailable")
    callback_rate_key = _rate_key("callback-ip", get_client_ip(request))
    callback_count = await redis.incr(callback_rate_key)
    if callback_count == 1:
        await redis.expire(callback_rate_key, _STATE_TTL_SECONDS)
    if callback_count > 60:
        return _oauth_failure_html("rate_limited")
    raw_state = await redis.get(_state_key(state))
    if not raw_state:
        return _oauth_failure_html("state_expired")
    lock_key = f"{_state_key(state)}:processing"
    if not await redis.set(lock_key, "1", ex=_CALLBACK_LOCK_SECONDS, nx=True):
        return _oauth_failure_html("duplicate")
    public_id_hint: str | None = None
    try:
        try:
            state_payload = json.loads(raw_state)
            tenant_id = uuid.UUID(state_payload["tenant_id"])
            benefit_id = uuid.UUID(state_payload["benefit_id"])
            scan_token = state_payload["scan_token"]
            consent_id = uuid.UUID(state_payload["consent_id"])
            pending_key = str(state_payload["pending_key"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
        scan_payload = verify_scan_token(scan_token, expected_tenant_id=str(tenant_id))
        if scan_payload is None:
            raise HTTPException(status_code=401, detail="expired scan credential")
        public_id_hint = str(scan_payload["public_id"])

        try:
            authority = require_consumer_scan_authority(scan_payload)
            await set_session_tenant_context(db, tenant_id)
            policy = await get_current_consumer_policy(db, tenant_id, "wechat_cash_payout")
            receipt = await get_consumer_consent_receipt_status(
                db,
                tenant_id=tenant_id,
                consent_id=consent_id,
                scan_event_id=authority.scan_event_id,
                scan_time=authority.scan_time,
                public_id=authority.public_id,
                visitor_id=authority.visitor_id,
                token_consumer_id=authority.consumer_id,
            )
            if (
                receipt.get("status") != "granted"
                or receipt.get("purpose") != "wechat_cash_payout"
                or receipt.get("policy_version") != policy["policy_version"]
                or receipt.get("policy_digest") != policy["policy_digest"]
            ):
                raise HTTPException(status_code=403, detail="consent_required")
            connector = await _cash_connector(db, tenant_id, benefit_id)
            # Receipt status deliberately holds a NOWAIT share lock. End this read-only
            # phase before the callback-only authority takes its write lock in another session.
            await db.commit()
            appid, secret = _oauth_credentials(connector)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    _TOKEN_URL,
                    params={"appid": appid, "secret": secret, "code": code, "grant_type": "authorization_code"},
                )
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="WeChat OAuth exchange failed")
            oauth_data = response.json()
            openid = oauth_data.get("openid")
            if not isinstance(openid, str) or not openid or oauth_data.get("errcode"):
                raise HTTPException(status_code=401, detail="WeChat OAuth was not granted")

            bound = await bind_wechat_oauth_consumer_authority(
                tenant_id=tenant_id,
                consent_id=consent_id,
                scan_event_id=authority.scan_event_id,
                scan_time=authority.scan_time,
                public_id=authority.public_id,
                visitor_id=authority.visitor_id,
                token_consumer_id=authority.consumer_id,
                openid=openid,
                benefit_id=benefit_id,
            )
            consumer_id = uuid.UUID(str(bound["consumer_id"]))
            await redis.delete(_state_key(state), pending_key)

            rebound = bind_scan_token_consumer(scan_payload, consumer_id, scan_payload.get("ip_hash"))
            h5_url = f"{settings.h5_public_url.rstrip('/')}/c/{quote(str(scan_payload['public_id']), safe='')}"
            fragment = urlencode(
                {
                    "scan_token": rebound,
                    "benefit_id": str(benefit_id),
                    "consent_id": str(consent_id),
                    "oauth": "success",
                }
            )
            return RedirectResponse(f"{h5_url}#{fragment}", status_code=303)
        except HTTPException as exc:
            # 可定位码页的失败回跳码页；state 无效等无法定位的已在上方提前返回 HTML
            if public_id_hint:
                return _oauth_failure_redirect_response(exc, public_id_hint)
            raise
    finally:
        await redis.delete(lock_key)


def _oauth_failure_redirect_response(exc: HTTPException, public_id: str) -> RedirectResponse:
    """把回调失败转换为携带 reason fragment 的 H5 码页回跳。"""

    reason_by_detail = {
        "expired scan credential": "scan_token_expired",
        "consent_required": "consent_required",
        "WeChat OAuth was not granted": "not_granted",
        "WeChat OAuth exchange failed": "exchange_failed",
    }
    reason = reason_by_detail.get(str(exc.detail)) or {
        429: "rate_limited",
        503: "service_unavailable",
    }.get(exc.status_code, "unknown")
    h5_url = f"{settings.h5_public_url.rstrip('/')}/c/{quote(public_id, safe='')}"
    return RedirectResponse(f"{h5_url}#oauth=failed&reason={reason}", status_code=303)


_OAUTH_FAILURE_TITLES = {
    "state_expired": "授权连接已过期",
    "duplicate": "授权正在处理中",
    "rate_limited": "操作过于频繁",
    "service_unavailable": "服务暂时不可用",
}


def _oauth_failure_html(reason: str) -> HTMLResponse:
    """无法定位原始码页时（如 state 过期）的兜底提示页。"""

    title = _OAUTH_FAILURE_TITLES.get(reason, "授权未完成")
    hint = "请返回微信重新扫码进入，即可重新领取权益。"
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        f"<title>{title}</title></head>"
        '<body style="margin:0;font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;'
        'background:#f5f6f7;display:flex;align-items:center;justify-content:center;min-height:100vh;">'
        '<div style="text-align:center;padding:32px 24px;">'
        f'<div style="font-size:40px;line-height:1;">⚠️</div>'
        f'<h1 style="font-size:18px;color:#1f2329;margin:16px 0 8px;">{title}</h1>'
        f'<p style="font-size:14px;color:#6b7280;margin:0;">{hint}</p>'
        "</div></body></html>"
    )
    return HTMLResponse(content=html, status_code=200)
