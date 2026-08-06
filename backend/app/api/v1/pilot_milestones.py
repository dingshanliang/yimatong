"""试点里程碑 API（beads: yimatong-bgag.1，PRD pilot-learning-retrospective §4.1/§4.5）。

只读：派生 + 持久化 + 返回本租户里程碑时间线。里程碑不可手工写入或编辑。
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.pilot_milestone import build_milestone_timeline
from app.utils.auth_rbac import require_permission

router = APIRouter(prefix="/api/v1/pilot-milestones", tags=["pilot-milestones"])


@router.get("", summary="获取本租户试点里程碑时间线")
async def get_milestone_timeline(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    """返回 5 个里程碑及其状态、派生时长（开通→上线、上线→首扫）。

    未达成里程碑返回"未达成"；事实源尚未捕获的返回"数据不足"。
    首次调用会幂等派生并持久化已达成里程碑，后续调用只读。
    """
    return await build_milestone_timeline(db, tenant_id)
