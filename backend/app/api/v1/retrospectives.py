"""试点复盘 API（beads: yimatong-bgag.2，PRD pilot-learning-retrospective §4.5）。

读取本租户复盘（含派生 overdue 状态）；填写/完成复盘（pending→completed，
完成后快照冻结，仅可追加 supplementary_notes）。
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.retrospective import RetrospectiveRead, RetrospectiveUpdateRequest
from app.services.retrospective import (
    _derived_status,
    get_retrospective,
    list_retrospectives,
    update_retrospective,
)
from app.utils.auth_rbac import require_permission

router = APIRouter(prefix="/api/v1/retrospectives", tags=["retrospectives"])


def _serialize(retro, now: datetime) -> RetrospectiveRead:
    return RetrospectiveRead(
        id=retro.id,
        tenant_id=retro.tenant_id,
        period_day=retro.period_day,
        window_start=retro.window_start,
        window_end=retro.window_end,
        next_review_date=retro.next_review_date,
        status=str(retro.status),
        derived_status=_derived_status(retro, now),
        goal=retro.goal,
        scorecard_snapshot=retro.scorecard_snapshot,
        issues=retro.issues,
        actions=retro.actions or [],
        completed_at=retro.completed_at,
        completed_by=retro.completed_by,
        supplementary_notes=retro.supplementary_notes,
        created_at=retro.created_at,
        updated_at=retro.updated_at,
    )


@router.get("", summary="获取本租户试点复盘列表")
async def list_retrospectives_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    now = datetime.now(UTC)
    retros = await list_retrospectives(db, tenant_id, now)
    return [_serialize(r, now) for r in retros]


@router.get("/{retro_id}", summary="获取单期复盘详情")
async def get_retrospective_endpoint(
    retro_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    now = datetime.now(UTC)
    retro = await get_retrospective(db, tenant_id, retro_id)
    if retro is None:
        raise HTTPException(status_code=404, detail="Retrospective not found")
    return _serialize(retro, now)


@router.patch("/{retro_id}", summary="填写/完成复盘")
async def update_retrospective_endpoint(
    retro_id: uuid.UUID,
    body: RetrospectiveUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    """填写或完成复盘。mark_completed=true 推进 pending→completed，冻结快照。

    状态机与可写字段判定全部在 service 层（update_retrospective），
    路由只做编排。
    """
    retro = await get_retrospective(db, tenant_id, retro_id)
    if retro is None:
        raise HTTPException(status_code=404, detail="Retrospective not found")

    actions_payload = [a.model_dump(mode="json") for a in body.actions] if body.actions is not None else None

    updated = await update_retrospective(
        db,
        tenant_id,
        retro_id,
        goal=body.goal,
        issues=body.issues,
        actions=actions_payload,
        next_review_date=body.next_review_date,
        supplementary_notes=body.supplementary_notes,
        mark_completed=body.mark_completed,
        actor_id=account_id,
    )

    if updated is None:
        raise HTTPException(status_code=404, detail="Retrospective not found")
    # 刷新以加载 server-side 生成的 updated_at/completed_at（避免序列化时触发 lazy IO）
    await db.refresh(updated)
    now = datetime.now(UTC)
    return _serialize(updated, now)
