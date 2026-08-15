"""试点里程碑 API（beads: yimatong-bgag.1 / bgag.7，PRD pilot-learning-retrospective §4.1/§4.5/§6.2）。

GET 只读：派生 + 持久化 + 返回本租户里程碑时间线（含更正记录）。
POST 更正：追加式更正记录，不改写原始事实（PRD §6.2）；仅平台管理员可执行（PRD §5）。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import get_db, get_db_with_bypass
from app.core.dependencies import get_current_tenant
from app.schemas.pilot_milestone import MilestoneCorrectionRequest
from app.services.pilot_access import (
    canonical_pilot_payload_digest,
    enforce_pilot_mutation_rate_limit,
    pilot_authority_http_error,
    require_pilot_read_access,
    require_platform_pilot_correction_access,
)
from app.services.pilot_milestone import build_milestone_timeline, correct_milestone

router = APIRouter(prefix="/api/v1/pilot-milestones", tags=["pilot-milestones"])
platform_router = APIRouter(prefix="/api/v1/platform/tenants", tags=["platform", "pilot-milestones"])
CanonicalIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]


@router.get("", summary="获取本租户试点里程碑时间线")
async def get_milestone_timeline(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    auth_session_id: uuid.UUID = Depends(require_pilot_read_access),
):
    """返回 5 个里程碑及其状态、派生时长（开通→上线、上线→首扫）与更正记录。

    未达成里程碑返回"未达成"；事实源尚未捕获的返回"数据不足"。
    首次调用会幂等派生并持久化已达成里程碑，后续调用只读。
    """
    return await build_milestone_timeline(db, tenant_id, auth_session_id=auth_session_id)


@platform_router.post("/{target_tenant_id}/pilot-milestones/corrections", summary="追加里程碑更正记录")
async def add_milestone_correction(
    target_tenant_id: uuid.UUID,
    body: MilestoneCorrectionRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db_with_bypass, scope="function"),
    platform_auth_session_id: uuid.UUID = Depends(require_platform_pilot_correction_access),
):
    """追加一条里程碑更正记录（PRD §6.2，仅平台管理员，PRD §5）。

    里程碑原始 achieved_at 不变；读取层在展示时以最新更正为有效值。
    事务边界由 get_db 依赖统一提交/回滚。
    """
    await enforce_pilot_mutation_rate_limit(target_tenant_id, platform_auth_session_id)
    request_id = uuid7()
    payload_digest = canonical_pilot_payload_digest(
        {"target_tenant_id": target_tenant_id, **body.model_dump(mode="python")}
    )
    try:
        correction = await correct_milestone(
            db,
            target_tenant_id,
            body.milestone_type,
            body.corrected_at,
            reason=body.reason,
            source=body.source,
            platform_auth_session_id=platform_auth_session_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DBAPIError as exc:
        raise pilot_authority_http_error(exc) from exc
    if correction is None:
        raise HTTPException(status_code=404, detail="该里程碑尚未达成，无可更正对象")
    return {
        "id": str(correction.id),
        "milestone_type": correction.milestone_type,
        "corrected_at": correction.corrected_at.isoformat(),
        "source": correction.source,
        "reason": correction.reason,
    }
