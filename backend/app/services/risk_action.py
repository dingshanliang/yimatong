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
from uuid6 import uuid7

from app.core.event_bus import event_bus
from app.core.exceptions import NotFoundError
from app.models.campaign import Campaign
from app.models.code import CodeItem
from app.models.risk import (
    InterceptionRecord,
    RiskAlert,
    RiskNotification,
    RiskRule,
)

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
        code_item_id=code_item.id if code_item is not None else None,
        action=rule.action,
        context=context,
        auto_triggered=True,
    )
    db.add(interception)
    await db.flush()

    if rule.action == "block":
        action_detail = await _execute_block(db, tenant_id, code_item, rule, interception)
    elif rule.action == "warn":
        action_detail = await _execute_warn(db, tenant_id, public_id, code_item, rule, context)

    # 更新拦截记录
    if rule.action != "block":
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

    # 广播到 SSE 实时告警
    try:
        from app.api.v1.risk_dashboard import _broadcast_alert

        _broadcast_alert(
            str(tenant_id),
            {
                "type": "risk_alert",
                "rule_name": rule.name,
                "rule_type": rule.rule_type,
                "action": rule.action,
                "public_id": public_id,
                "interception_id": str(interception.id),
                "steps": action_detail.get("steps", []),
            },
        )
    except Exception:
        logger.warning("SSE broadcast failed", exc_info=True)


async def _execute_block(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_item: CodeItem | None,
    rule: RiskRule,
    interception: InterceptionRecord,
) -> dict:
    """block 动作：冻结码项 + 暂停关联活动。"""
    if code_item is None:
        raise NotFoundError("Risk code item not found")

    from app.services.code import freeze_code_item_for_risk

    result = await freeze_code_item_for_risk(
        db,
        tenant_id,
        interception.id,
        code_item.id,
        uuid7(),
        uuid7(),
    )
    steps: list[dict] = [
        {
            "action": "freeze_code",
            "status": "success",
            "code_item_id": str(result["code_item_id"]),
            "before": {"status": result["prior_status"]},
            "after": {"status": result["current_status"]},
            "risk_alert_id": str(result["risk_alert_id"]),
            "audit_id": str(result["audit_id"]),
            "rule_id": str(rule.id),
            "interception_id": str(interception.id),
        }
    ]

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
    """warn 动作：创建风险预警（yimatong-zgb1.7：不冻结码，但 risk_level=medium 阻断权益）。"""
    steps: list[dict] = []

    if code_item is None:
        raise NotFoundError("Risk code item not found")

    alert = RiskAlert(
        tenant_id=tenant_id,
        alert_type="auto_warn",
        public_id=public_id,
        code_item_id=code_item.id,
        detail=f"风控规则 [{rule.name}] 自动触发预警",
        # yimatong-zgb1.7 Decision 16+17：warn 级不冻结码（保留溯源），
        # 但 risk_level=medium 会被 claim_benefit 门禁拦截（AC3）。
        risk_level="medium",
        rule_name=rule.name,
        rule_version=str(rule.config.get("version", "v1")) if rule.config else "v1",
        evidence_quality=_evidence_quality_for_rule(rule.rule_type),
    )
    db.add(alert)
    await db.flush()
    steps.append({"action": "create_alert", "status": "success", "alert_id": str(alert.id)})

    return {"steps": steps}


def _evidence_quality_for_rule(rule_type: str) -> str:
    """yimatong-zgb1.7：根据规则类型推断证据质量（Decision 17）。

    - ip_frequency / cross_region：基于 IP/位置的统计信号，medium（可能误判）
    - suspected_copy / multi_location：基于多设备/多地的强信号，strong
    - 其他：medium（保守）
    """
    strong_types = {"suspected_copy", "multi_location", "device_frequency"}
    if rule_type in strong_types:
        return "strong"
    return "medium"


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

    batch_result = await db.execute(select(CodeBatch).where(CodeBatch.id == code_item.code_batch_id))
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
        # 为每个暂停的活动发事件。注意：当前复用 campaign.ended 事件名以便 webhook
        # dispatcher（订阅 campaign.ended）能投递暂停通知；payload.status=PAUSED 区分
        # 可恢复暂停与终态结束。若改为 campaign.paused 需同步在 webhook_dispatcher 的
        # event_types 中注册，否则暂停通知会丢失（见 RU09-F02）。
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
