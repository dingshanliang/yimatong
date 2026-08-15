"""权益发放状态查询端点（H5，scan_token/回访凭证鉴权，只读派生）。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import rate_limiter
from app.services.benefit_claim_status import resolve_consumer_claim_status
from app.services.redis_cache import SharedSecurityCacheUnavailable

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

    # 共享原子限流（fail-closed）：Redis 不可用时拒绝服务而不是回退进程内存
    # 计数（否则每个 worker 各自计数，配额形同虚设）；与公开解析端点语义一致。
    try:
        rate_result = await rate_limiter.check_shared(
            f"claim-status:{claim_id}", CLAIM_STATUS_RATE_LIMIT, CLAIM_STATUS_RATE_WINDOW_SECONDS
        )
    except SharedSecurityCacheUnavailable:
        raise HTTPException(status_code=503, detail="暂时无法查询，请稍后再试") from None
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

    status = await resolve_consumer_claim_status(db, token, claim_id)
    if status is None:
        raise _unavailable()

    return {
        "status": status.status,
        "amount_minor": status.amount_minor,
        "completed_at": status.completed_at.isoformat() if status.completed_at else None,
        "failure_reason": status.failure_reason,
    }
