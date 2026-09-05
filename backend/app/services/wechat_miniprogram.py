"""Shared WeChat mini-program identity verification boundary."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.core.config import settings

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


@dataclass(frozen=True)
class VerifiedMiniProgramIdentity:
    issuer: str
    openid: str


async def exchange_miniprogram_code(js_code: str) -> VerifiedMiniProgramIdentity:
    """Exchange a short-lived wx.login code without exposing provider secrets or subjects."""

    appid = settings.shared_wechat_miniprogram_appid.strip()
    secret = settings.shared_wechat_miniprogram_secret.strip()
    if not appid or not secret:
        raise HTTPException(status_code=503, detail="shared_miniprogram_not_configured")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                WECHAT_CODE2SESSION_URL,
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": js_code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="wechat_identity_unavailable") from exc

    if payload.get("errcode") or not isinstance(payload.get("openid"), str) or not payload["openid"].strip():
        raise HTTPException(status_code=401, detail="invalid_wechat_login_code")
    return VerifiedMiniProgramIdentity(issuer=appid, openid=payload["openid"].strip())
