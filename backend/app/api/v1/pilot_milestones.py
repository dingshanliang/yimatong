"""试点里程碑 API（beads: yimatong-bgag.1 / bgag.7，PRD pilot-learning-retrospective §4.1/§4.5/§6.2）。

GET 只读：派生 + 持久化 + 返回本租户里程碑时间线（含更正记录）。
POST 更正：追加式更正记录，不改写原始事实（PRD §6.2）；仅平台管理员可执行（PRD §5）。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.pilot_milestone import MilestoneCorrectionRequest
from app.services.pilot_milestone import build_milestone_timeline, correct_milestone
from app.utils.auth_rbac import require_permission, require_role

router = APIRouter(prefix="/api/v1/pilot-milestones", tags=["pilot-milestones"])


@router.get("", summary="获取本租户试点里程碑时间线")
async def get_milestone_timeline(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    """返回 5 个里程碑及其状态、派生时长（开通→上线、上线→首扫）与更正记录。

    未达成里程碑返回"未达成"；事实源尚未捕获的返回"数据不足"。
    首次调用会幂等派生并持久化已达成里程碑，后续调用只读。
    """
    return await build_milestone_timeline(db, tenant_id)


@router.post("/corrections", summary="追加里程碑更正记录（不改写原始事实）")
async def add_milestone_correction(
    body: MilestoneCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("platform_admin")),
    _permission: None = Depends(require_permission("pilot:manage")),
):
    """追加一条里程碑更正记录（PRD §6.2，仅平台管理员，PRD §5）。

    里程碑原始 achieved_at 不变；读取层在展示时以最新更正为有效值。
    事务边界由 get_db 依赖统一提交/回滚。
    """
    correction = await correct_milestone(
        db,
        tenant_id,
        body.milestone_type,
        body.corrected_at,
        reason=body.reason,
        source=body.source,
        corrected_by=actor_id,
    )
    if correction is None:
        raise HTTPException(status_code=404, detail="该里程碑尚未达成，无可更正对象")
    return {
        "id": str(correction.id),
        "milestone_type": correction.milestone_type,
        "corrected_at": correction.corrected_at.isoformat(),
        "source": correction.source,
        "reason": correction.reason,
    }
