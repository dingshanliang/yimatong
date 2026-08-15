"""回访凭证：scan_token 时效之后恢复查询单笔领取状态的服务端签发物。

凭证只绑定（租户, claim, 领取者主体）三元组与到期时间，不含 PII，
也不授予该 claim 之外的任何读权限。
"""

from __future__ import annotations

import time
import uuid

import jwt

from app.core.config import settings

REVISIT_CREDENTIAL_TYPE = "claim_revisit"
# 回访窗口取多天级：覆盖"领取当天没等到账、隔天回来看"的主场景，
# 且远短于任何业务留存期。
REVISIT_CREDENTIAL_TTL_SECONDS = 7 * 24 * 3600


def issue_revisit_credential(
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    consumer_id: str,
    expires_in: int = REVISIT_CREDENTIAL_TTL_SECONDS,
) -> str:
    payload = {
        "type": REVISIT_CREDENTIAL_TYPE,
        "tenant_id": str(tenant_id),
        "claim_id": str(claim_id),
        "consumer_id": consumer_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_revisit_credential(
    token: str,
    expected_claim_id: uuid.UUID,
    expected_tenant_id: uuid.UUID | None = None,
) -> dict | None:
    """校验签名、时效与 claim 绑定；任一不满足返回 None（与不可查询语义对齐）。

    expected_tenant_id 为可选的纵深防御：端点场景下 claim 绑定已隐含租户
    （claim 行按租户过滤查询），凭证签发时的租户仍记录在 payload 中。
    """

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.PyJWTError:
        return None
    if payload.get("type") != REVISIT_CREDENTIAL_TYPE:
        return None
    if payload.get("claim_id") != str(expected_claim_id):
        return None
    if expected_tenant_id is not None and payload.get("tenant_id") != str(expected_tenant_id):
        return None
    consumer_id = payload.get("consumer_id")
    if not isinstance(consumer_id, str) or not consumer_id.strip():
        return None
    return payload
