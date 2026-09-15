"""微盟（Weimob）开放平台 API 协议工具。

协议常量与算法全部隔离在本模块，真实店铺联调校准只改这里。
契约基线见 docs/02_tech/INTEGRATION_PLAYBOOK.md §7.2，以 https://doc.weimobcloud.com 为准；
当前状态为 pending_external：WOS v2.0 接口契约经官方文档冻结，
推送 sign 的 msgBody 精确序列化形式等细节需真实联调逐项校准。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings

DEFAULT_API_BASE_URL = "https://dopen.weimob.com"
API_TIMEOUT_SECONDS = 10.0

# WOS v2.0 命名空间路径：`POST {base}{path}?accesstoken=TOKEN`
CUSTOMER_IMPORT_PATH = "/apigw/weimob_crm/v2.0/customer/import"
COUPON_RECEIVE_PATH = "/apigw/weimob_crm/v2.0/coupon/receive"
COUPON_GETLIST_PATH = "/apigw/weimob_crm/v2.0/coupon/getList"

# 自用型正式店铺仅支持客户端凭证模式，无 refresh_token
TOKEN_ENDPOINT = "/fuwu/b/oauth2/token"
GRANT_TYPE_CLIENT_CREDENTIALS = "client_credentials"

# coupon/receive 的 scene 固定值：945000 = API 发券
API_SCENE = 945000

# access_token 有效期 7 天（expires_in=604799）；client_credentials 可随时重取，
# 提前 24 小时进入刷新窗口，避免发放路径撞上过期
TOKEN_REFRESH_BUFFER_SECONDS = 86400


class WeimobAPIError(Exception):
    """微盟网关返回的确定性错误（HTTP 层或业务 errcode 层）。"""

    def __init__(self, *, status_code: int, code: Any, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"weimob api error status={status_code} code={code} message={message}")


def _md5_lower(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


async def fetch_token(
    *,
    client_id: str,
    client_secret: str,
    shop_id: str,
    shop_type: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """客户端凭证模式获取 access_token：`POST {base}/fuwu/b/oauth2/token`（参数走 query string）。

    返回至少包含 access_token、expires_in；自用型正式店铺无 refresh_token，
    token 临期由调用方直接重取（无单次有效刷新令牌的烧毁风险）。
    """
    base = (base_url or settings.weimob_api_base_url).rstrip("/")
    params: dict[str, str] = {
        "grant_type": GRANT_TYPE_CLIENT_CREDENTIALS,
        "client_id": client_id,
        "client_secret": client_secret,
        "shop_id": shop_id,
        "shop_type": shop_type,
    }
    async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{base}{TOKEN_ENDPOINT}", params=params)
    if response.status_code != 200:
        raise WeimobAPIError(
            status_code=response.status_code,
            code=None,
            message=f"weimob token endpoint http {response.status_code}",
        )
    body = response.json()
    if not body.get("access_token"):
        code_block = body.get("code") or {}
        raise WeimobAPIError(
            status_code=200,
            code=code_block.get("errcode") if isinstance(code_block, dict) else body.get("errcode"),
            message=str(code_block.get("errmsg") if isinstance(code_block, dict) else body.get("errmsg"))
            or "weimob token response missing access_token",
        )
    return body


def _extract_success_payload(body: dict[str, Any]) -> dict[str, Any]:
    code_block = body.get("code")
    if isinstance(code_block, dict):
        errcode = code_block.get("errcode")
        if errcode not in (0, None):
            raise WeimobAPIError(
                status_code=200,
                code=errcode,
                message=str(code_block.get("errmsg") or "weimob gateway business error"),
            )
    if isinstance(body.get("data"), dict):
        return body["data"]
    return body


async def call_weimob_api(
    *,
    path: str,
    accesstoken: str,
    json_body: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """调用微盟 WOS v2.0 接口 `POST {base}{path}?accesstoken=TOKEN`，返回成功业务载荷。

    传输层异常（超时、连接失败）原样抛出 httpx 异常，由适配器映射为 pending；
    HTTP 非 200 或业务 errcode 抛 WeimobAPIError，由适配器按 5xx/4xx 区分三态。
    """
    base = (base_url or settings.weimob_api_base_url).rstrip("/")
    url = f"{base}{path}"
    async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
        response = await client.post(url, params={"accesstoken": accesstoken}, json=json_body or {})
    if response.status_code != 200:
        raise WeimobAPIError(
            status_code=response.status_code,
            code=None,
            message=f"weimob gateway http {response.status_code}",
        )
    return _extract_success_payload(response.json())


def token_expiry_iso(expires_in: int | float | str) -> str:
    """把 expires_in（秒）换算成绝对过期时刻（ISO 字符串），供 secrets 持久化。"""
    return (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat()


def token_expired(expires_at: str | None, *, buffer_seconds: int = TOKEN_REFRESH_BUFFER_SECONDS) -> bool:
    """token 是否已失效或进入刷新缓冲期。缺省视为失效，强制走重取。"""
    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return datetime.now(UTC) >= expires - timedelta(seconds=buffer_seconds)


def _msg_body_candidates(msg_body: Any) -> list[str]:
    """sign 计算参与的 msgBody 候选串（联调校准点：官方未明示序列化形式）。

    推送示例中 msgBody 是 JSON 对象而类型标注为 String，sign 生成时可能取
    原始串或某种序列化串；这里穷举合理候选，逐项 compare_digest。
    """
    if isinstance(msg_body, str):
        return [msg_body]
    if not isinstance(msg_body, dict):
        return []
    return [
        json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False),
        json.dumps(msg_body, separators=(",", ":")),
        json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False, sort_keys=True),
        json.dumps(msg_body, ensure_ascii=False),
    ]


def verify_push_signature(
    *,
    client_id: str,
    client_secret: str,
    msg_id: Any,
    msg_body: Any,
    sign: str,
) -> bool:
    """微盟消息推送验签：sign = md5(clientId + id + msgBody + clientSecret)。

    与有赞的密钥包裹式不同，这里是四段顺序拼接；msgBody 序列化形式未在文档
    明示，逐候选比对（见 _msg_body_candidates）。
    """
    if not sign or msg_id is None:
        return False
    provided = sign.strip().lower()
    for candidate in _msg_body_candidates(msg_body):
        expected = _md5_lower(f"{client_id}{msg_id}{candidate}{client_secret}")
        if hmac.compare_digest(expected, provided):
            return True
    return False


def parse_push_payload(body: bytes) -> tuple[dict[str, Any] | None, str]:
    """解析微盟消息推送体，返回 (信封, sign)。

    微盟推送为 application/json，信封结构 `{id, topic, event, bosId, sign, msgBody}`；
    解析失败或非对象返回 (None, "")，由适配器 verify_callback 拒绝。
    """
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None, ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    return payload, str(payload.get("sign") or "")


def push_event_fields(msg: dict[str, Any]) -> tuple[str, str]:
    """从推送信封提取 (topic, event)，如 ("weimob_crm.coupon", "receiveCoupon")。"""
    topic = msg.get("topic")
    event = msg.get("event")
    return (
        topic if isinstance(topic, str) else "",
        event if isinstance(event, str) else "",
    )


def push_event_data(msg: dict[str, Any]) -> dict[str, Any]:
    """从推送信封提取 msgBody（对象或 JSON 字符串均兼容）。"""
    msg_body = msg.get("msgBody", msg.get("msg_body"))
    if isinstance(msg_body, dict):
        return msg_body
    if isinstance(msg_body, str) and msg_body:
        try:
            parsed = json.loads(msg_body)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
