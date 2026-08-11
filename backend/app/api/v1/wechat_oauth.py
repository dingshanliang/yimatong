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
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import get_db_for_consumer, set_session_tenant_context
from app.models.campaign import Benefit
from app.models.connector import Connector
from app.models.consent import ConsentRecord, ConsentStatus, ConsentType
from app.models.member import ConsumerProfile
from app.services.audit import write_audit_log
from app.services.connectors.secrets import decrypt_secrets
from app.services.consent import grant_consent
from app.services.redis_cache import get_redis_pool
from app.services.scan_token import bind_scan_token_consumer, verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip
from app.utils.crypto import encrypt_wechat_openid, hash_wechat_openid

wechat_oauth_router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-oauth"])

_AUTHORIZE_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
_STATE_TTL_SECONDS = 300
_CALLBACK_LOCK_SECONDS = 30
_OAUTH_POLICY_VERSION = "2026-08-11-v1"


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


async def _bind_openid(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    scan_payload: dict,
    openid: str,
) -> ConsumerProfile:
    openid_hash = hash_wechat_openid(tenant_id, openid)
    consumer: ConsumerProfile | None = None
    consumer_id = scan_payload.get("consumer_id")
    if isinstance(consumer_id, str) and consumer_id:
        try:
            consumer = await db.scalar(
                select(ConsumerProfile).where(
                    ConsumerProfile.id == uuid.UUID(consumer_id),
                    ConsumerProfile.tenant_id == tenant_id,
                )
            )
        except ValueError:
            consumer = None
        if consumer is None:
            raise HTTPException(status_code=401, detail="invalid consumer identity")
        if consumer.wechat_openid_hash not in (None, openid_hash):
            raise HTTPException(status_code=409, detail="consumer is already bound to another WeChat account")
    else:
        consumer = await db.scalar(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.wechat_openid_hash == openid_hash,
            )
        )
        if consumer is None:
            consumer = ConsumerProfile(id=uuid7(), tenant_id=tenant_id)
            db.add(consumer)

    ciphertext, nonce, key_id = encrypt_wechat_openid(tenant_id, consumer.id, openid)
    consumer.wechat_openid_hash = openid_hash
    consumer.wechat_openid_ciphertext = ciphertext
    consumer.wechat_openid_nonce = nonce
    consumer.wechat_openid_key_id = key_id
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="WeChat account is already bound") from exc
    return consumer


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

    public_id = str(payload.get("public_id") or "")
    token_jti = str(payload.get("jti") or "")
    if not public_id or not token_jti:
        raise HTTPException(status_code=401, detail="scan credential lacks OAuth authority")
    try:
        token_consumer_id = uuid.UUID(payload["consumer_id"]) if payload.get("consumer_id") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid consumer identity") from exc
    redis = await get_redis_pool()
    if redis is None:
        raise HTTPException(status_code=503, detail="OAuth state service unavailable")
    state = secrets.token_hex(32)
    pending_key = _pending_key(token_jti)
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
            consent = await grant_consent(
                db,
                tenant_id,
                ConsentType.privacy,
                public_id=public_id,
                consumer_id=token_consumer_id,
                ip_hash=payload.get("ip_hash"),
                scenario="wechat_cash_payout",
                policy_version=_OAUTH_POLICY_VERSION,
                user_agent=request.headers.get("user-agent"),
            )
            await db.commit()
            state_payload = json.dumps(
                {
                    "tenant_id": str(tenant_id),
                    "benefit_id": str(body.benefit_id),
                    "scan_token": body.scan_token,
                    "consent_id": str(consent.id),
                    "pending_key": pending_key,
                },
                separators=(",", ":"),
            )
            await redis.setex(_state_key(state), _STATE_TTL_SECONDS, state_payload)
        except Exception:
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

    redis = await get_redis_pool()
    if redis is None:
        raise HTTPException(status_code=503, detail="OAuth state service unavailable")
    callback_rate_key = _rate_key("callback-ip", get_client_ip(request))
    callback_count = await redis.incr(callback_rate_key)
    if callback_count == 1:
        await redis.expire(callback_rate_key, _STATE_TTL_SECONDS)
    if callback_count > 60:
        raise HTTPException(status_code=429, detail="too many OAuth callback attempts")
    raw_state = await redis.get(_state_key(state))
    if not raw_state:
        raise HTTPException(status_code=401, detail="invalid or expired OAuth state")
    lock_key = f"{_state_key(state)}:processing"
    if not await redis.set(lock_key, "1", ex=_CALLBACK_LOCK_SECONDS, nx=True):
        raise HTTPException(status_code=409, detail="OAuth state is already being processed")
    try:
        state_payload = json.loads(raw_state)
        tenant_id = uuid.UUID(state_payload["tenant_id"])
        benefit_id = uuid.UUID(state_payload["benefit_id"])
        scan_token = state_payload["scan_token"]
        consent_id = uuid.UUID(state_payload["consent_id"])
        pending_key = str(state_payload["pending_key"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        await redis.delete(lock_key)
        raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
    scan_payload = verify_scan_token(scan_token, expected_tenant_id=str(tenant_id))
    if scan_payload is None:
        await redis.delete(lock_key)
        raise HTTPException(status_code=401, detail="expired scan credential")

    try:
        await set_session_tenant_context(db, tenant_id)
        consent = await db.scalar(
            select(ConsentRecord).where(
                ConsentRecord.id == consent_id,
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.public_id == scan_payload["public_id"],
                ConsentRecord.consent_type == ConsentType.privacy,
                ConsentRecord.scenario == "wechat_cash_payout",
                ConsentRecord.policy_version == _OAUTH_POLICY_VERSION,
                ConsentRecord.status == ConsentStatus.granted,
                ConsentRecord.withdrawn_at.is_(None),
            )
        )
        if consent is None:
            raise HTTPException(status_code=403, detail="consent_required")
        connector = await _cash_connector(db, tenant_id, benefit_id)
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

        consumer = await _bind_openid(db, tenant_id, scan_payload, openid)
        consent.consumer_id = consumer.id
        await db.flush()

        await write_audit_log(
            db,
            str(consumer.id),
            str(tenant_id),
            "consumer_wechat_bound",
            f"consumer_profile:{consumer.id}",
            {"benefit_id": str(benefit_id), "public_id": scan_payload["public_id"], "consent_id": str(consent.id)},
        )
        await db.commit()
        await redis.delete(_state_key(state), pending_key)

        rebound = bind_scan_token_consumer(scan_payload, consumer.id, scan_payload.get("ip_hash"))
        h5_url = f"{settings.h5_public_url.rstrip('/')}/c/{quote(scan_payload['public_id'], safe='')}"
        fragment = urlencode(
            {
                "scan_token": rebound,
                "benefit_id": str(benefit_id),
                "consent_id": str(consent.id),
                "oauth": "success",
            }
        )
        return RedirectResponse(f"{h5_url}#{fragment}", status_code=303)
    finally:
        await redis.delete(lock_key)
