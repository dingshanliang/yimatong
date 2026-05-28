"""风控规则引擎服务"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import (
    CampaignRiskRule,
    InterceptionRecord,
    RiskRule,
    RiskRuleAction,
)


async def create_risk_rule(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, rule_type: str,
    action: str, config: dict,
) -> RiskRule:
    rule = RiskRule(
        tenant_id=tenant_id,
        name=name,
        rule_type=rule_type,
        action=action,
        config=config,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def list_risk_rules(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[RiskRule]:
    result = await db.execute(
        select(RiskRule).where(RiskRule.tenant_id == tenant_id).order_by(RiskRule.id.desc())
    )
    return list(result.scalars().all())


async def get_risk_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID,
) -> RiskRule | None:
    result = await db.execute(
        select(RiskRule).where(RiskRule.id == rule_id, RiskRule.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def update_risk_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID,
    **updates,
) -> RiskRule:
    rule = await get_risk_rule(db, tenant_id, rule_id)
    if not rule:
        raise ValueError("Risk rule not found")
    for k, v in updates.items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_risk_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID,
) -> bool:
    rule = await get_risk_rule(db, tenant_id, rule_id)
    if not rule:
        raise ValueError("Risk rule not found")
    await db.delete(rule)
    await db.commit()
    return True


def _evaluate_rule(rule: RiskRule, context: dict) -> bool:
    """根据规则配置和上下文判断是否触发"""
    config = rule.config
    rt = rule.rule_type

    if rt == "ip_frequency":
        return context.get("request_count", 0) >= config.get("max_requests", 999)
    elif rt == "phone_frequency":
        return context.get("request_count", 0) >= config.get("max_requests", 999)
    elif rt == "device_frequency":
        return context.get("request_count", 0) >= config.get("max_requests", 999)
    elif rt == "time_window":
        current_hour = context.get("current_hour")
        allowed = config.get("allowed_hours", [])
        return current_hour not in allowed
    elif rt == "region_restriction":
        allowed = config.get("allowed_regions", [])
        detected = context.get("detected_region", "")
        return detected not in allowed if allowed else False
    elif rt == "budget_limit":
        return context.get("current_spend", 0) >= config.get("max_budget", float("inf"))
    elif rt == "stock_limit":
        return context.get("stock_used", 0) >= config.get("max_stock", float("inf"))
    return False


async def evaluate_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_type: str, context: dict,
    consumer_id: str | None = None,
) -> dict:
    """评估指定类型的所有启用规则"""
    result = await db.execute(
        select(RiskRule).where(
            RiskRule.tenant_id == tenant_id,
            RiskRule.rule_type == rule_type,
            RiskRule.enabled.is_(True),
        )
    )
    rules = list(result.scalars().all())

    for rule in rules:
        if _evaluate_rule(rule, context):
            record = InterceptionRecord(
                tenant_id=tenant_id,
                risk_rule_id=rule.id,
                action=rule.action,
                context=context,
                consumer_id=consumer_id,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return {
                "triggered": True,
                "action": rule.action,
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "interception_id": str(record.id),
            }

    return {"triggered": False, "action": None}


async def attach_rule_to_campaign(
    db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID, campaign_id: uuid.UUID,
) -> CampaignRiskRule:
    link = CampaignRiskRule(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        risk_rule_id=rule_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def evaluate_campaign_rules(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID, context: dict,
) -> dict:
    """评估活动关联的所有启用规则"""
    result = await db.execute(
        select(CampaignRiskRule).where(
            CampaignRiskRule.campaign_id == campaign_id,
            CampaignRiskRule.tenant_id == tenant_id,
        )
    )
    links = list(result.scalars().all())
    rule_ids = [l.risk_rule_id for l in links]

    if not rule_ids:
        return {"triggered": False, "action": None, "rules_evaluated": 0}

    rules_result = await db.execute(
        select(RiskRule).where(
            RiskRule.id.in_(rule_ids),
            RiskRule.enabled.is_(True),
        )
    )
    rules = list(rules_result.scalars().all())

    for rule in rules:
        if _evaluate_rule(rule, context):
            record = InterceptionRecord(
                tenant_id=tenant_id,
                risk_rule_id=rule.id,
                campaign_id=campaign_id,
                action=rule.action,
                context=context,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return {
                "triggered": True,
                "action": rule.action,
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "interception_id": str(record.id),
                "rules_evaluated": len(rules),
            }

    return {"triggered": False, "action": None, "rules_evaluated": len(rules)}


async def list_interceptions(
    db: AsyncSession, tenant_id: uuid.UUID,
    action: str | None = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[InterceptionRecord], int]:
    stmt = select(InterceptionRecord).where(
        InterceptionRecord.tenant_id == tenant_id,
    )
    count_stmt = select(func.count()).select_from(InterceptionRecord).where(
        InterceptionRecord.tenant_id == tenant_id,
    )

    if action:
        stmt = stmt.where(InterceptionRecord.action == action)
        count_stmt = count_stmt.where(InterceptionRecord.action == action)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(InterceptionRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
