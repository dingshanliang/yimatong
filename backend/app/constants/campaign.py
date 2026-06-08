"""活动模块常量和枚举定义"""

from enum import StrEnum


class CampaignStatus(StrEnum):
    """活动状态枚举（str 子类，可直接与字符串比较）"""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class BenefitType(StrEnum):
    """权益类型枚举"""

    PLATFORM_COUPON = "platform_coupon"
    EXTERNAL_LINK = "external_link"
    PRIVATE_DOMAIN = "private_domain"
    FORM_BENEFIT = "form_benefit"
    CASH_RED_PACKET = "cash_red_packet"


# ── 常量集合 ──────────────────────────────────

CAMPAIGN_GOALS = {
    "first_scan_coupon",
    "lottery",
    "points",
    "private_domain_repurchase",
    "festival",
    "custom",
}

PARTICIPATION_CONDITION_TYPES = {
    "first_scan",
    "any_scan",
    "member_only",
}

BENEFIT_VALIDITY_TYPES = {
    "campaign_period",
    "after_claim_days",
    "fixed_range",
}

WECOM_MODES = {"none", "guide", "required"}

BENEFIT_TYPES = {
    "platform_coupon",
    "external_link",
    "private_domain",
    "form_benefit",
    "cash_red_packet",
}

BENEFIT_STATUSES = {"active", "inactive"}

CASH_RED_PACKET_AMOUNT_TYPES = {"fixed", "random", "lucky"}

# 合法活动状态转换表
ALLOWED_CAMPAIGN_TRANSITIONS: dict[str, list[str]] = {
    CampaignStatus.DRAFT: [CampaignStatus.ACTIVE],
    CampaignStatus.ACTIVE: [CampaignStatus.PAUSED, CampaignStatus.ENDED],
    CampaignStatus.PAUSED: [CampaignStatus.ACTIVE, CampaignStatus.ENDED],
    CampaignStatus.ENDED: [],  # 不可逆
}

# 可更新字段白名单（防止 mass assignment）
UPDATABLE_CAMPAIGN_FIELDS = {
    "name", "campaign_type", "start_at", "end_at", "rules_json", "description",
}

UPDATABLE_BENEFIT_FIELDS = {
    "name", "benefit_type", "config_json", "stock_total",
    "per_person_limit", "connector_id", "status", "campaign_id",
}

# campaign_type 合法值
CAMPAIGN_TYPES = {
    "coupon", "discount", "trial", "presale", "event",
    "lottery", "points", "private_domain_repurchase", "festival", "custom",
}

# 计算状态常量
COMPUTED_STATUS_PENDING = "pending"
COMPUTED_STATUS_VALUES = {"draft", "active", "paused", "ended", "pending"}
