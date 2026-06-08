"""活动与权益配置验证工具函数"""

from datetime import datetime

from app.constants.campaign import (
    BENEFIT_TYPES,
    BENEFIT_VALIDITY_TYPES,
    CAMPAIGN_GOALS,
    CASH_RED_PACKET_AMOUNT_TYPES,
    PARTICIPATION_CONDITION_TYPES,
    WECOM_MODES,
)


def validate_campaign_rules_shape(rules_json: dict) -> dict:
    campaign_goal = rules_json.get("campaign_goal")
    if campaign_goal is not None and campaign_goal not in CAMPAIGN_GOALS:
        raise ValueError(f"campaign_goal must be one of: {', '.join(sorted(CAMPAIGN_GOALS))}")

    participation_type = rules_json.get("participation_condition_type")
    if participation_type is not None and participation_type not in PARTICIPATION_CONDITION_TYPES:
        raise ValueError(
            f"participation_condition_type must be one of: {', '.join(sorted(PARTICIPATION_CONDITION_TYPES))}"
        )

    wecom_mode = rules_json.get("wecom_mode", "none")
    if wecom_mode is not None and wecom_mode not in WECOM_MODES:
        raise ValueError(f"wecom_mode must be one of: {', '.join(sorted(WECOM_MODES))}")

    claim_limit_count = rules_json.get("claim_limit_count")
    if claim_limit_count is not None:
        if not isinstance(claim_limit_count, int) or claim_limit_count < 1:
            raise ValueError("claim_limit_count must be an integer greater than or equal to 1")

    return rules_json


def validate_benefit_config_shape(config_json: dict, benefit_type: str | None = None) -> dict:
    if benefit_type is not None and benefit_type not in BENEFIT_TYPES:
        raise ValueError(f"benefit_type must be one of: {', '.join(sorted(BENEFIT_TYPES))}")

    campaign_goal = config_json.get("campaign_goal")
    if campaign_goal is not None and campaign_goal not in CAMPAIGN_GOALS:
        raise ValueError(f"campaign_goal must be one of: {', '.join(sorted(CAMPAIGN_GOALS))}")

    validity_type = config_json.get("validity_type")
    if validity_type is not None and validity_type not in BENEFIT_VALIDITY_TYPES:
        raise ValueError(f"validity_type must be one of: {', '.join(sorted(BENEFIT_VALIDITY_TYPES))}")

    if validity_type == "after_claim_days":
        validity_days = config_json.get("validity_days")
        if not isinstance(validity_days, int) or validity_days < 1:
            raise ValueError("validity_days must be an integer greater than or equal to 1")

    if validity_type == "fixed_range":
        start_at = _parse_config_datetime(config_json.get("validity_start_at"), "validity_start_at")
        end_at = _parse_config_datetime(config_json.get("validity_end_at"), "validity_end_at")
        if end_at < start_at:
            raise ValueError("validity_end_at must be later than or equal to validity_start_at")

    if benefit_type == "platform_coupon":
        amount = config_json.get("amount")
        if amount is not None and (not isinstance(amount, int | float) or amount < 0):
            raise ValueError("amount must be a number greater than or equal to 0")
        min_order = config_json.get("min_order")
        if min_order is not None and (not isinstance(min_order, int | float) or min_order < 0):
            raise ValueError("min_order must be a number greater than or equal to 0")

    if benefit_type == "external_link":
        _require_url(config_json, "url")

    if benefit_type == "private_domain":
        _require_url(config_json, "qr_image_url")

    if benefit_type == "form_benefit":
        _require_url(config_json, "form_url")
        if "require_phone" in config_json and not isinstance(config_json["require_phone"], bool):
            raise ValueError("require_phone must be a boolean")

    if benefit_type == "cash_red_packet":
        amount_type = config_json.get("amount_type")
        if amount_type not in CASH_RED_PACKET_AMOUNT_TYPES:
            raise ValueError(f"amount_type must be one of: {', '.join(sorted(CASH_RED_PACKET_AMOUNT_TYPES))}")
        _require_positive_number(config_json, "budget")
        if amount_type == "fixed":
            _require_positive_number(config_json, "fixed_amount", max_value=20000)
        if amount_type == "random":
            min_amount = _require_positive_number(config_json, "min_amount", max_value=20000)
            max_amount = _require_positive_number(config_json, "max_amount", max_value=20000)
            if max_amount < min_amount:
                raise ValueError("max_amount must be greater than or equal to min_amount")
        if amount_type == "lucky":
            _require_positive_number(config_json, "lucky_min_per", max_value=20000)
            total_count = config_json.get("lucky_total_count")
            if not isinstance(total_count, int) or total_count < 2:
                raise ValueError("lucky_total_count must be an integer greater than or equal to 2")

    return config_json


def _require_url(config_json: dict, field_name: str) -> str:
    value = config_json.get(field_name)
    if not isinstance(value, str) or not value.strip().lower().startswith(("http://", "https://")):
        raise ValueError(f"{field_name} must be an http(s) URL")
    return value.strip()


def _require_positive_number(config_json: dict, field_name: str, max_value: int | None = None) -> int | float:
    value = config_json.get(field_name)
    if not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{field_name} must be a number greater than 0")
    if max_value is not None and value > max_value:
        raise ValueError(f"{field_name} must be less than or equal to {max_value}")
    return value


def _parse_config_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid datetime") from exc
