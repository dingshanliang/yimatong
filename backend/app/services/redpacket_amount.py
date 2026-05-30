"""红包金额计算服务。

支持三种模式：
- fixed: 固定金额
- random: 随机金额（min ~ max）
- lucky: 拼手气（二倍均值法，限量 N 份）
"""

from __future__ import annotations

import random

WECHAT_CASH_MARKETING_MAX = 20000  # 微信现金营销单笔上限（分）= 200 元
MIN_AMOUNT = 10  # 最小金额（分）= 0.1 元


def validate_config(config: dict) -> tuple[bool, str]:
    """校验红包 config_json 合法性。"""
    amount_type = config.get("amount_type")
    if amount_type not in ("fixed", "random", "lucky"):
        return False, "amount_type must be one of: fixed, random, lucky"

    budget = config.get("budget")
    if not budget or budget <= 0:
        return False, "budget must be positive"

    if amount_type == "fixed":
        fixed_amount = config.get("fixed_amount")
        if not fixed_amount or fixed_amount < MIN_AMOUNT:
            return False, f"fixed_amount must be >= {MIN_AMOUNT} (0.1元)"
        if fixed_amount > WECHAT_CASH_MARKETING_MAX:
            return False, f"fixed_amount must be <= {WECHAT_CASH_MARKETING_MAX} (200元)"

    elif amount_type == "random":
        min_a = config.get("min_amount")
        max_a = config.get("max_amount")
        if not min_a or min_a < MIN_AMOUNT:
            return False, f"min_amount must be >= {MIN_AMOUNT}"
        if not max_a or max_a > WECHAT_CASH_MARKETING_MAX:
            return False, f"max_amount must be <= {WECHAT_CASH_MARKETING_MAX}"
        if min_a > max_a:
            return False, "min_amount must be <= max_amount"

    elif amount_type == "lucky":
        count = config.get("lucky_total_count")
        if not count or count <= 0:
            return False, "lucky_total_count must be positive"
        min_per = config.get("lucky_min_per", MIN_AMOUNT)
        if min_per < MIN_AMOUNT:
            return False, f"lucky_min_per must be >= {MIN_AMOUNT}"
        # 确保总预算够分
        if budget < count * min_per:
            return False, "budget too small for lucky_total_count * lucky_min_per"

    daily_limit = config.get("daily_limit_per_user", 3)
    total_limit = config.get("total_limit_per_user", 10)
    if daily_limit < 1 or total_limit < 1:
        return False, "daily_limit_per_user and total_limit_per_user must be >= 1"

    return True, ""


def calc_fixed(config: dict) -> int:
    """返回固定金额（分）。"""
    return config["fixed_amount"]


def calc_random(config: dict) -> int:
    """返回随机金额（分）。"""
    return random.randint(config["min_amount"], config["max_amount"])


def calc_lucky(
    config: dict,
    remaining_budget: int,
    remaining_count: int,
) -> int:
    """二倍均值法计算拼手气金额（分）。

    规则：
    - 如果只剩 1 份，返回全部剩余预算
    - 否则在 [min_per, 2 * avg] 范围内随机
    """
    if remaining_count <= 0:
        return 0
    if remaining_count == 1:
        return remaining_budget

    min_per = config.get("lucky_min_per", MIN_AMOUNT)
    avg = remaining_budget / remaining_count
    upper = int(avg * 2)

    # 确保不超过剩余预算（减去后面每份的最小值）
    max_for_this = remaining_budget - (remaining_count - 1) * min_per
    upper = min(upper, max_for_this)
    upper = max(upper, min_per)

    # 不超过微信限额
    upper = min(upper, WECHAT_CASH_MARKETING_MAX)

    amount = random.randint(min_per, upper)
    return min(amount, remaining_budget)


def calc_amount(config: dict, remaining_budget: int | None = None, remaining_count: int | None = None) -> int:
    """根据 amount_type 自动选择计算方式。"""
    amount_type = config.get("amount_type", "random")

    if amount_type == "fixed":
        return calc_fixed(config)
    elif amount_type == "random":
        return calc_random(config)
    elif amount_type == "lucky":
        if remaining_budget is None or remaining_count is None:
            raise ValueError("lucky mode requires remaining_budget and remaining_count")
        return calc_lucky(config, remaining_budget, remaining_count)
    else:
        raise ValueError(f"Unknown amount_type: {amount_type}")
