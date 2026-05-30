"""红包金额计算服务单元测试。"""

import pytest

from app.services.redpacket_amount import (
    WECHAT_CASH_MARKETING_MAX,
    calc_amount,
    calc_fixed,
    calc_lucky,
    calc_random,
    validate_config,
)


class TestValidateConfig:
    def test_fixed_valid(self):
        ok, msg = validate_config({
            "amount_type": "fixed",
            "fixed_amount": 100,
            "budget": 10000,
        })
        assert ok
        assert msg == ""

    def test_fixed_too_large(self):
        ok, msg = validate_config({
            "amount_type": "fixed",
            "fixed_amount": WECHAT_CASH_MARKETING_MAX + 1,
            "budget": 100000,
        })
        assert not ok
        assert "200元" in msg

    def test_random_valid(self):
        ok, msg = validate_config({
            "amount_type": "random",
            "min_amount": 30,
            "max_amount": 300,
            "budget": 10000,
        })
        assert ok

    def test_random_min_gt_max(self):
        ok, msg = validate_config({
            "amount_type": "random",
            "min_amount": 300,
            "max_amount": 30,
            "budget": 10000,
        })
        assert not ok
        assert "min_amount must be <=" in msg

    def test_lucky_valid(self):
        ok, msg = validate_config({
            "amount_type": "lucky",
            "lucky_total_count": 10,
            "lucky_min_per": 10,
            "budget": 5000,
        })
        assert ok

    def test_lucky_budget_too_small(self):
        ok, msg = validate_config({
            "amount_type": "lucky",
            "lucky_total_count": 100,
            "lucky_min_per": 10,
            "budget": 500,
        })
        assert not ok
        assert "budget too small" in msg

    def test_invalid_amount_type(self):
        ok, msg = validate_config({
            "amount_type": "unknown",
            "budget": 1000,
        })
        assert not ok
        assert "amount_type must be one of" in msg

    def test_zero_budget(self):
        ok, msg = validate_config({
            "amount_type": "fixed",
            "fixed_amount": 100,
            "budget": 0,
        })
        assert not ok
        assert "budget must be positive" in msg


class TestCalcFixed:
    def test_returns_fixed_amount(self):
        assert calc_fixed({"fixed_amount": 188}) == 188

    def test_min_amount(self):
        assert calc_fixed({"fixed_amount": 10}) == 10


class TestCalcRandom:
    def test_within_range(self):
        config = {"min_amount": 30, "max_amount": 300}
        for _ in range(100):
            amount = calc_random(config)
            assert 30 <= amount <= 300

    def test_single_value_range(self):
        config = {"min_amount": 100, "max_amount": 100}
        assert calc_random(config) == 100


class TestCalcLucky:
    def test_last_person_gets_all(self):
        config = {"lucky_min_per": 10, "lucky_total_count": 5}
        amount = calc_lucky(config, remaining_budget=500, remaining_count=1)
        assert amount == 500

    def test_zero_remaining_count(self):
        config = {"lucky_min_per": 10}
        amount = calc_lucky(config, remaining_budget=0, remaining_count=0)
        assert amount == 0

    def test_amounts_distribute_within_budget(self):
        config = {"lucky_min_per": 10, "lucky_total_count": 10}
        total_budget = 5000
        remaining = total_budget
        amounts = []
        for i in range(10):
            amount = calc_lucky(config, remaining_budget=remaining, remaining_count=10 - i)
            amounts.append(amount)
            remaining -= amount

        assert sum(amounts) == total_budget
        assert all(a >= 10 for a in amounts)
        assert all(a <= WECHAT_CASH_MARKETING_MAX for a in amounts)

    def test_respects_wechat_limit(self):
        config = {"lucky_min_per": 10, "lucky_total_count": 2}
        # Budget is huge, but single amount should be capped
        amount = calc_lucky(config, remaining_budget=100000, remaining_count=2)
        assert amount <= WECHAT_CASH_MARKETING_MAX


class TestCalcAmount:
    def test_auto_selects_fixed(self):
        config = {"amount_type": "fixed", "fixed_amount": 100}
        assert calc_amount(config) == 100

    def test_auto_selects_random(self):
        config = {"amount_type": "random", "min_amount": 50, "max_amount": 50}
        assert calc_amount(config) == 50

    def test_auto_selects_lucky(self):
        config = {"amount_type": "lucky", "lucky_min_per": 10}
        amount = calc_amount(config, remaining_budget=100, remaining_count=2)
        assert amount > 0
        assert amount <= 100

    def test_lucky_missing_params_raises(self):
        config = {"amount_type": "lucky", "lucky_min_per": 10}
        with pytest.raises(ValueError, match="lucky mode requires"):
            calc_amount(config)
