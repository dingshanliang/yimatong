"""风控处置动作执行器。

规则命中后执行具体的处置动作：
- block: 冻结码项 + 暂停关联活动 + 发送通知
- warn: 创建风险预警 + 发送通知

所有处置结果记录到 InterceptionRecord.action_detail。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.code import CodeItem, CodeItemStatus
from app.models.campaign import Campaign
from app.models.risk import (
    InterceptionRecord,
    RiskAlert,
    RiskAlertType,
    RiskNotification,
    RiskRule,
)
from app.services.code_state import can_transition

logger = logging.getLogger(__name__)


async def execute_risk_action(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule: RiskRule,
    public_id: str,
    code_item: CodeItem | None,
    context: dict,
) -> None:
    """执行风控处置动作。"""
    action_detail: dict = {"steps": []}

    # 记录拦截记录（自动触发）
    interception = InterceptionRecord(
        tenant_id=tenant_id,
        risk_rule_id=rule.id,
        action=rule.action,
        context=context,
        auto_triggered=True,
    )
    db.add(interception)
    await db.flush()

    if rule.action == "block":
        action_detail = await _execute_block(db, tenant_id, public_id, code_item, context)
    elif rule.action == "warn":
        action_detail = await _execute_warn(db, tenant_id, public_id, code_item, rule, context)

    # 更新拦截记录
    interception.action_taken = rule.action
    interception.action_detail = action_detail
    await db.flush()

    # 发送通知
    await _create_notification(
        db=db,
        tenant_id=tenant_id,
        rule=rule,
        public_id=public_id,
        code_item=code_item,
        action_taken=rule.action,
        action_detail=action_detail,
    )

    # 发射事件
    await event_bus.emit(
        "risk.action_executed",
        {
            "rule_id": str(rule.id),
            "rule_name": rule.name,
            "action": rule.action,
            "public_id": public_id,
            "interception_id": str(interception.id),
        },
        str(tenant_id),
    )


async def _execute_block(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    code_item: CodeItem | None,
    context: dict,
) -> dict:
    """block 动作：冻结码项 + 暂停关联活动。"""
    steps: list[dict] = []

    # 1. 冻结码项
    if code_item and code_item.status in (CodeItemStatus.activated, CodeItemStatus.bound):
        try:
            can_transition(code_item.status, CodeItemStatus.frozen, raise_on_invalid=True)
            code_item.status = CodeItemStatus.frozen
            steps.append({"action": "freeze_code", "status": "success", "code_item_id": str(code_item.id)})

            alert = RiskAlert(
                tenant_id=tenant_id,
                alert_type=RiskAlertType.risk_frozen,
                public_id=public_id,
                code_item_id=code_item.id,
                detail="风控规则自动触发：码已被冻结",
            )
            db.add(alert)
        except Exception as e:
            steps.append({"action": "freeze_code", "status": "failed", "error": str(e)})
    else:
        steps.append({"action": "freeze_code", "status": "skipped", "reason": "code_not_found_or_not_freezable"})

    # 2. 查找并暂停关联活动（通过码的码批次 → 产品 → 关联活动）
    paused_campaigns = await _pause_related_campaigns(db, tenant_id, code_item)
    if paused_campaigns:
        steps.append({"action": "pause_campaigns", "status": "success", "campaign_ids": paused_campaigns})
    else:
        steps.append({"action": "pause_campaigns", "status": "skipped", "reason": "no_active_campaigns_found"})

    return {"steps": steps}


async def _execute_warn(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    code_item: CodeItem | None,
    rule: RiskRule,
    context: dict,
) -> dict:
    """warn 动作：创建风险预警。"""
    steps: list[dict] = []

    alert = RiskAlert(
        tenant_id=tenant_id,
        alert_type="auto_warn",
        public_id=public_id,
        code_item_id=code_item.id if code_item else uuid.uuid4(),
        detail=f"风控规则 [{rule.name}] 自动触发预警",
    )
    db.add(alert)
    await db.flush()
    steps.append({"action": "create_alert", "status": "success", "alert_id": str(alert.id)})

    return {"steps": steps}


async def _pause_related_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_item: CodeItem | None,
) -> list[str]:
    """查找并暂停与码关联的活动（通过产品间接关联）。"""
    if not code_item:
        return []

    # 查找使用同一产品的活动
    from app.models.code import CodeBatch

    batch_result = await db.execute(
        select(CodeBatch).where(CodeBatch.id == code_item.code_batch_id)
    )
    batch = batch_result.scalar_one_or_none()
    if not batch:
        return []

    product_id = batch.product_id

    # 查找该产品的 active 活动（通过 rules_json 中 product 关联）
    # 简化方案：暂停所有该租户的 active 活动（生产环境需要更精确的关联）
    campaigns_result = await db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        )
    )
    campaigns = list(campaigns_result.scalars().all())

    paused_ids: list[str] = []
    for c in campaigns:
        # 检查活动的 rules_json 是否包含该产品
        rules = c.rules_json or {}
        product_ids = rules.get("product_ids", [])
        if str(product_id) in product_ids or not product_ids:
            c.status = "paused"
            paused_ids.append(str(c.id))

    if paused_ids:
        await db.flush()
        # 为每个暂停的活动发事件
        for cid in paused_ids:
            await event_bus.emit(
                "campaign.ended",
                {"campaign_id": cid, "status": "PAUSED", "reason": "risk_auto_pause"},
                str(tenant_id),
            )

    return paused_ids


async def _create_notification(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule: RiskRule,
    public_id: str,
    code_item: CodeItem | None,
    action_taken: str,
    action_detail: dict,
) -> None:
    """创建 Admin 风控通知。"""
    action_label = "阻断" if action_taken == "block" else "预警"
    title = f"风控自动{action_label}：{rule.name}"

    steps = action_detail.get("steps", [])
    detail_parts = [f"码 {public_id} 触发规则 [{rule.rule_type}]"]
    for step in steps:
        if step.get("status") == "success":
            detail_parts.append(f"  → 已执行: {step['action']}")
        elif step.get("status") == "failed":
            detail_parts.append(f"  → 执行失败: {step['action']}")

    notification = RiskNotification(
        tenant_id=tenant_id,
        notification_type=f"risk_{action_taken}",
        title=title,
        detail="\n".join(detail_parts),
        risk_rule_id=rule.id,
        campaign_id=None,
        code_item_id=code_item.id if code_item else None,
        read=False,
    )
    db.add(notification)
    await db.flush()
