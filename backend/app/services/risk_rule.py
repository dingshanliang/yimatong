"""Read-only risk projections and pure rule evaluation helpers."""

import uuid

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import InterceptionRecord, RiskCampaignPause, RiskRule
from app.schemas.risk_rule import _CONFIG_MODEL


def normalize_rule_config(rule_type: str, config: dict) -> dict:
    model = _CONFIG_MODEL.get(rule_type)
    if model is None:
        raise ValidationError.from_exception_data("RiskRuleConfig", [])
    return model.model_validate(config).model_dump()


async def list_risk_rules(db: AsyncSession, tenant_id: uuid.UUID) -> list[RiskRule]:
    result = await db.execute(select(RiskRule).where(RiskRule.tenant_id == tenant_id).order_by(RiskRule.id.desc()))
    return list(result.scalars().all())


async def get_risk_rule(
    db: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID, *, refresh: bool = False
) -> RiskRule | None:
    stmt = select(RiskRule).where(RiskRule.id == rule_id, RiskRule.tenant_id == tenant_id)
    if refresh:
        stmt = stmt.execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


def _evaluate_rule(rule: RiskRule, context: dict) -> bool:
    """Pure preview helper. Authoritative execution happens in PostgreSQL."""
    config = rule.config
    rule_type = rule.rule_type
    if rule_type in {"ip_frequency", "phone_frequency", "device_frequency"}:
        return context.get("request_count", 0) >= config.get("max_requests", 999)
    if rule_type == "time_window":
        return context.get("current_hour") not in config.get("allowed_hours", [])
    if rule_type == "region_restriction":
        allowed = config.get("allowed_regions", [])
        return context.get("detected_region", "") not in allowed if allowed else False
    if rule_type == "cross_region":
        return bool(context.get("cross_region_detected")) and context.get("cross_region_count", 0) >= config.get(
            "threshold_count", 1
        )
    if rule_type == "budget_limit":
        return context.get("current_spend", 0) >= config.get("max_budget", float("inf"))
    if rule_type == "stock_limit":
        return context.get("stock_used", 0) >= config.get("max_stock", float("inf"))
    return False


async def list_interceptions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    action: str | None = None,
    auto_triggered: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[InterceptionRecord], int]:
    filters = [InterceptionRecord.tenant_id == tenant_id]
    if action:
        filters.append(InterceptionRecord.action == action)
    if auto_triggered is not None:
        filters.append(InterceptionRecord.auto_triggered == auto_triggered)
    total = (await db.execute(select(func.count()).select_from(InterceptionRecord).where(*filters))).scalar() or 0
    result = await db.execute(
        select(InterceptionRecord)
        .where(*filters)
        .order_by(InterceptionRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def list_campaign_pauses(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[RiskCampaignPause], int]:
    filters = [RiskCampaignPause.tenant_id == tenant_id]
    if status is not None:
        filters.append(RiskCampaignPause.status == status)
    total = (await db.execute(select(func.count()).select_from(RiskCampaignPause).where(*filters))).scalar() or 0
    result = await db.execute(
        select(RiskCampaignPause)
        .where(*filters)
        .order_by(RiskCampaignPause.paused_at.desc(), RiskCampaignPause.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total
