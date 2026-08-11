"""页面 DSL Schema — 对齐前端 page-dsl.ts 的模块化结构

后端 Pydantic 校验，确保 config_json 入库前符合 DSL 规范。
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field, model_validator


# ── 模块类型枚举（与前端 ModuleType 一一对应）───────────────
class ModuleType(enum.StrEnum):
    product_hero = "product_hero"
    verification_status = "verification_status"
    light_traceability = "light_traceability"
    test_reports = "test_reports"
    certificates = "certificates"
    benefit_card = "benefit_card"
    cta_group = "cta_group"
    shop_redirect = "shop_redirect"
    lead_form = "lead_form"
    media_section = "media_section"
    legal_terms = "legal_terms"
    custom_html = "custom_html"
    member_card = "member_card"
    points_balance = "points_balance"
    points_exchange = "points_exchange"
    points_shop = "points_shop"
    outer_code_guide = "outer_code_guide"
    risk_alert = "risk_alert"
    dual_code_verify = "dual_code_verify"
    points_history = "points_history"


class ModuleConfigSchema(BaseModel):
    """单个模块配置"""

    id: str = Field(..., min_length=1, max_length=100)
    type: ModuleType
    enabled: bool = True
    config: dict | None = None

    @model_validator(mode="after")
    def reject_authoritative_facts(self) -> ModuleConfigSchema:
        """Page DSL owns presentation only; verification and risk facts come from the resolver."""
        config = self.config or {}
        forbidden_by_type = {
            ModuleType.risk_alert: {"alert_type", "detail", "scan_count", "detected_city"},
            ModuleType.dual_code_verify: {"product_verified"},
        }
        forbidden = forbidden_by_type.get(self.type, set()).intersection(config)
        if forbidden:
            raise ValueError(f"authoritative fields are not allowed in page DSL: {', '.join(sorted(forbidden))}")
        return self


class CampaignPeriodSchema(BaseModel):
    """活动期配置"""

    campaign_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    mode: str = Field("campaign", pattern="^(campaign|evergreen)$")


class RoutingConfigSchema(BaseModel):
    """页面路由配置"""

    default_page: bool = True
    campaign_periods: list[CampaignPeriodSchema] | None = None


class PageDSLSchema(BaseModel):
    """页面 DSL 顶层结构 — config_json 的后端校验模型"""

    modules: list[ModuleConfigSchema] = Field(default_factory=list, max_length=50)
    routing: RoutingConfigSchema | None = None

    model_config = {"extra": "allow"}


def validate_page_dsl(config_json: dict) -> dict:
    """校验 config_json 是否符合页面 DSL 规范。

    Args:
        config_json: 待校验的配置字典

    Returns:
        校验后的字典（Pydantic 已做类型强制转换）

    Raises:
        pydantic.ValidationError: 校验失败
    """
    validated = PageDSLSchema.model_validate(config_json)
    return validated.model_dump()
