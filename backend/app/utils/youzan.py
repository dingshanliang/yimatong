"""有赞开放平台 API 签名与协议工具。

协议常量与算法全部隔离在本模块，真实店铺联调校准只改这里。
契约基线见 docs/02_tech/INTEGRATION_PLAYBOOK.md §6.2，以 https://doc.youzanyun.com 为准；
当前状态为 pending_external：以下接口名、参数名、响应字段名经官方文档与社区资料交叉整理，
接入真实店铺前需逐项校准。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.core.config import settings

DEFAULT_API_BASE_URL = "https://open.youzanyun.com"
API_TIMEOUT_SECONDS = 10.0

COUPON_TAKE_METHOD = "youzan.ump.coupon.take"
COUPON_VERIFY_LOGS_METHOD = "kdt.ump.coupon.consume.verifylogs.get"
API_VERSION = "3.0.0"

TOKEN_ENDPOINT = "/auth/token"
# 自用型（工具型）应用可静默获取 token；刷新用 refresh_token
GRANT_TYPE_SILENT = "authorize"
GRANT_TYPE_REFRESH = "refresh_token"

# access_token 有效期 7 天（604800s），提前 1 小时刷新
TOKEN_REFRESH_BUFFER_SECONDS = 3600


class YouzanAPIError(Exception):
    """有赞网关返回的确定性错误（HTTP 层或业务 code 层）。"""

    def __init__(self, *, status_code: int, code: Any, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"youzan api error status={status_code} code={code} message={message}")


def _md5_upper(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def sign_request(params: dict[str, Any], client_secret: str) -> str:
    """有赞网关请求签名：参数按 key ASCII 升序拼接 k1v1k2v2...，首尾包 client_secret 后 MD5 大写。"""
    concat = "".join(f"{key}{params[key]}" for key in sorted(params) if key != "sign")
    return _md5_upper(f"{client_secret}{concat}{client_secret}")


def verify_push_signature(msg: str, sign: str, client_secret: str) -> bool:
    """有赞消息推送验签：与请求签名同构（密钥包裹 msg 原文）。"""
    expected = _md5_upper(f"{client_secret}{msg}{client_secret}")
    return hmac.compare_digest(expected, sign.strip().upper())


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def build_common_params(
    *,
    app_id: str,
    method: str,
    version: str = API_VERSION,
    access_token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "app_id": app_id,
        "method": method,
        "version": version,
        "timestamp": _timestamp(),
        "format": "json",
        "v": "1.0",
        "sign_method": "md5",
    }
    if access_token:
        params["access_token"] = access_token
    return params


def _extract_success_payload(body: dict[str, Any]) -> dict[str, Any]:
    code = body.get("code")
    if code not in (0, 200) and body.get("success") is not True:
        raise YouzanAPIError(
            status_code=200,
            code=code,
            message=str(body.get("message") or body.get("msg") or "youzan gateway business error"),
        )
    for key in ("result", "data", "response"):
        if isinstance(body.get(key), dict):
            return body[key]
    return body


async def call_youzan_api(
    *,
    method: str,
    params: dict[str, Any],
    client_id: str,
    client_secret: str,
    access_token: str | None = None,
    base_url: str | None = None,
    version: str = API_VERSION,
) -> dict[str, Any]:
    """调用有赞网关 `POST {base}/api/{method}/{version}`，返回成功业务载荷。

    传输层异常（超时、连接失败）原样抛出 httpx 异常，由适配器映射为 pending；
    HTTP 非 200 或业务错误码抛 YouzanAPIError，由适配器按 5xx/4xx 区分三态。
    """
    base = (base_url or settings.youzan_api_base_url).rstrip("/")
    request_params = build_common_params(app_id=client_id, method=method, version=version, access_token=access_token)
    request_params.update({k: v for k, v in params.items() if v is not None})
    request_params["sign"] = sign_request(request_params, client_secret)

    url = f"{base}/api/{method}/{version}"
    async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=request_params)
    if response.status_code != 200:
        raise YouzanAPIError(
            status_code=response.status_code,
            code=None,
            message=f"youzan gateway http {response.status_code}",
        )
    return _extract_success_payload(response.json())


async def fetch_token(
    *,
    client_id: str,
    client_secret: str,
    grant_type: str = GRANT_TYPE_SILENT,
    refresh_token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """获取/刷新 access_token：`POST {base}/auth/token`（form 编码）。

    返回至少包含 access_token、expires_in、refresh_token；调用方负责持久化。
    refresh_token 单次有效，调用方必须先落库再视为刷新成功。
    """
    base = (base_url or settings.youzan_api_base_url).rstrip("/")
    form: dict[str, str] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": grant_type,
    }
    if refresh_token:
        form["refresh_token"] = refresh_token

    async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{base}{TOKEN_ENDPOINT}", data=form)
    if response.status_code != 200:
        raise YouzanAPIError(
            status_code=response.status_code,
            code=None,
            message=f"youzan token endpoint http {response.status_code}",
        )
    body = response.json()
    if not body.get("access_token"):
        raise YouzanAPIError(
            status_code=200,
            code=body.get("code"),
            message=str(body.get("message") or body.get("msg") or "youzan token response missing access_token"),
        )
    return body


def token_expiry_iso(expires_in: int | float | str) -> str:
    """把 expires_in（秒）换算成绝对过期时刻（ISO 字符串），供 secrets 持久化。"""
    return (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat()


def token_expired(expires_at: str | None, *, buffer_seconds: int = TOKEN_REFRESH_BUFFER_SECONDS) -> bool:
    """token 是否已失效或进入刷新缓冲期。缺省视为失效，强制走刷新。"""
    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return datetime.now(UTC) >= expires - timedelta(seconds=buffer_seconds)


def parse_push_payload(body: bytes) -> tuple[dict[str, Any] | None, str, str]:
    """解析有赞消息推送体，返回 (msg 解析结果, msg 原文, sign)。

    有赞推送可能是 JSON body 或 form 编码（`msg=...&sign=...`），两种都兼容；
    解析失败返回 (None, "", "")，由适配器 verify_callback 拒绝。
    """
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None, "", ""
    raw_msg, sign = "", ""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            raw_msg = str(payload.get("msg") or "")
            sign = str(payload.get("sign") or "")
    except json.JSONDecodeError:
        form = parse_qs(text, keep_blank_values=True)
        raw_msg = (form.get("msg") or [""])[0]
        sign = (form.get("sign") or [""])[0]
    if not raw_msg:
        return None, "", ""
    try:
        msg = json.loads(raw_msg)
    except json.JSONDecodeError:
        return None, raw_msg, sign
    return msg, raw_msg, sign


def push_event_type(msg: dict[str, Any]) -> str:
    """从推送 msg 中提取事件类型标识（联调校准点：字段名以真实推送为准）。"""
    for key in ("type", "topic", "event", "mode"):
        value = msg.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def push_event_data(msg: dict[str, Any]) -> dict[str, Any]:
    """从推送 msg 中提取业务数据（联调校准点：字段名以真实推送为准）。"""
    for key in ("data", "msg", "body"):
        value = msg.get(key)
        if isinstance(value, dict):
            return value
    return {}
