"""权益发放状态查询端点（H5，scan_token 鉴权，只读派生）。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, set_session_tenant_context
from app.middleware.rate_limit import rate_limiter
from app.services.benefit_claim_admission import build_claim_consumer_id
from app.services.benefit_claim_status import get_consumer_claim_status
from app.services.scan_token import verify_scan_token

benefit_claim_status_router = APIRouter(prefix="/api/v1", tags=["benefit-claims"])

# 独立配额（claim 维度）：指数退避轮询远低于该阈值；不复用领取的 IP 桶或扫码的码桶。
CLAIM_STATUS_RATE_LIMIT = 60
CLAIM_STATUS_RATE_WINDOW_SECONDS = 60


def _unavailable() -> HTTPException:
    """防枚举：token 无效、主体不匹配、租户不符、记录不存在共用同一语义。"""

    return HTTPException(
        status_code=404,
        detail={"code": "claim_status_unavailable", "message": "该领取记录当前不可查询"},
    )


@benefit_claim_status_router.get("/benefit-claims/{claim_id}/status")
async def get_benefit_claim_status(
    request: Request,
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """消费者查询自己某笔领取的发放状态（processing/success/failed，终态单调）。"""

    rate_result = await rate_limiter.check(
        f"claim-status:{claim_id}", CLAIM_STATUS_RATE_LIMIT, CLAIM_STATUS_RATE_WINDOW_SECONDS
    )
    if not rate_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="scan_token required")

    # 只读端点不校验 ip_hash：轮询跨网络切换不应中断；身份以 token 主体绑定为准。
    payload = verify_scan_token(token)
    if payload is None:
        raise _unavailable()

    try:
        tenant_id = uuid.UUID(str(payload.get("tenant_id")))
    except (TypeError, ValueError):
        raise _unavailable() from None
    try:
        consumer_id = build_claim_consumer_id(payload)
    except ValueError:
        raise _unavailable() from None

    tenant_id = await set_session_tenant_context(db, tenant_id)

    status = await get_consumer_claim_status(db, tenant_id, claim_id, consumer_id)
    if status is None:
        raise _unavailable()

    return {
        "status": status.status,
        "amount_minor": status.amount_minor,
        "completed_at": status.completed_at.isoformat() if status.completed_at else None,
        "failure_reason": status.failure_reason,
    }
