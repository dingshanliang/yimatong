"""Fail-closed stubs for the excluded WeChat cash-red-packet OAuth flow."""

from fastapi import APIRouter, HTTPException

wechat_oauth_router = APIRouter(prefix="/api/v1/wechat", tags=["wechat-oauth"])

_UNSUPPORTED_DETAIL = "WeChat red-packet OAuth is not supported"


@wechat_oauth_router.get("/auth-url")
async def get_auth_url(benefit_id: str, scan_token: str):
    """Reject the excluded OAuth entrypoint without reading tenant data."""
    del benefit_id, scan_token
    raise HTTPException(status_code=404, detail=_UNSUPPORTED_DETAIL)


@wechat_oauth_router.get("/oauth-callback")
async def oauth_callback(code: str, state: str):
    """Reject legacy callbacks without exchanging credentials or mutating data."""
    del code, state
    raise HTTPException(status_code=404, detail=_UNSUPPORTED_DETAIL)
