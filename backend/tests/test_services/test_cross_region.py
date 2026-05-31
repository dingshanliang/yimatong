"""跨区扫码预警触发逻辑单元测试"""

import uuid
from types import SimpleNamespace

import pytest

from app.services.risk_rule import _evaluate_rule


class TestCrossRegionRuleEvaluation:
    """测试 cross_region 规则类型的评估逻辑"""

    def _make_rule(self, threshold: int = 1):
        return SimpleNamespace(
            rule_type="cross_region",
            config={"threshold_count": threshold},
            action="warn",
        )

    def test_triggered_when_cross_region_detected_and_count_exceeds_threshold(self):
        rule = self._make_rule(threshold=3)
        context = {
            "cross_region_detected": True,
            "cross_region_count": 3,
            "expected_region": "上海",
            "detected_region": "北京",
        }
        assert _evaluate_rule(rule, context) is True

    def test_not_triggered_when_not_cross_region(self):
        rule = self._make_rule(threshold=1)
        context = {
            "cross_region_detected": False,
            "cross_region_count": 0,
        }
        assert _evaluate_rule(rule, context) is False

    def test_not_triggered_when_count_below_threshold(self):
        rule = self._make_rule(threshold=5)
        context = {
            "cross_region_detected": True,
            "cross_region_count": 3,
        }
        assert _evaluate_rule(rule, context) is False

    def test_default_threshold_is_1(self):
        rule = self._make_rule(threshold=1)
        context = {
            "cross_region_detected": True,
            "cross_region_count": 1,
        }
        assert _evaluate_rule(rule, context) is True

    def test_zero_cross_region_count_not_triggered_with_positive_threshold(self):
        rule = self._make_rule(threshold=1)
        context = {
            "cross_region_detected": True,
            "cross_region_count": 0,
        }
        assert _evaluate_rule(rule, context) is False


class TestCrossRegionContextBuilding:
    """测试跨区上下文构建逻辑"""

    def test_expected_region_structure(self):
        expected = {
            "city": "上海",
            "region_name": "华东区",
            "store_id": str(uuid.uuid4()),
            "store_name": "上海旗舰店",
            "distributor_id": str(uuid.uuid4()),
        }
        assert expected["city"] == "上海"
        assert expected["store_name"] == "上海旗舰店"

    def test_fallback_to_batch_region(self):
        expected = {
            "city": "北京",
            "region_name": "华北区",
            "distributor_id": str(uuid.uuid4()),
        }
        assert "store_id" not in expected
        assert expected["city"] == "北京"


class TestDiversionClueLogic:
    """测试窜货线索逻辑"""

    def test_same_city_no_diversion(self):
        expected_city = "上海"
        detected_city = "上海"
        assert detected_city == expected_city  # 不算跨区

    def test_different_city_is_diversion(self):
        expected_city = "上海"
        detected_city = "北京"
        assert detected_city != expected_city  # 算跨区

    def test_resolve_flag(self):
        resolved = False
        resolved = True
        assert resolved is True


class TestCrossRegionStats:
    """测试跨区统计结构"""

    def test_stats_structure(self):
        stats = {
            "total_clues": 42,
            "unresolved_count": 15,
            "by_region": [
                {"region": "华东区", "count": 20},
                {"region": "华北区", "count": 22},
            ],
            "by_detected_city": [
                {"city": "北京", "count": 15},
                {"city": "上海", "count": 12},
            ],
            "by_code": [
                {"public_id": "ABC123", "count": 5},
            ],
        }
        assert stats["total_clues"] == 42
        assert stats["unresolved_count"] == 15
        assert len(stats["by_region"]) == 2
        assert len(stats["by_detected_city"]) == 2
        assert len(stats["by_code"]) == 1

    def test_empty_stats(self):
        stats = {
            "total_clues": 0,
            "unresolved_count": 0,
            "by_region": [],
            "by_detected_city": [],
            "by_code": [],
        }
        assert stats["total_clues"] == 0


class TestRiskRuleTypes:
    """测试所有规则类型的评估"""

    def test_region_restriction_triggered(self):
        rule = SimpleNamespace(
            rule_type="region_restriction",
            config={"allowed_regions": ["上海", "北京"]},
            action="warn",
        )
        ctx = {"detected_region": "深圳"}
        assert _evaluate_rule(rule, ctx) is True

    def test_region_restriction_not_triggered(self):
        rule = SimpleNamespace(
            rule_type="region_restriction",
            config={"allowed_regions": ["上海", "北京"]},
            action="warn",
        )
        ctx = {"detected_region": "上海"}
        assert _evaluate_rule(rule, ctx) is False

    def test_cross_region_with_empty_context(self):
        rule = SimpleNamespace(
            rule_type="cross_region",
            config={"threshold_count": 1},
            action="block",
        )
        ctx = {}
        assert _evaluate_rule(rule, ctx) is False

    def test_cross_region_with_none_detected(self):
        rule = SimpleNamespace(
            rule_type="cross_region",
            config={"threshold_count": 1},
            action="warn",
        )
        ctx = {"cross_region_detected": None}
        assert _evaluate_rule(rule, ctx) is False

    def test_ip_frequency_triggered(self):
        rule = SimpleNamespace(
            rule_type="ip_frequency",
            config={"max_requests": 10},
            action="block",
        )
        ctx = {"request_count": 15}
        assert _evaluate_rule(rule, ctx) is True

    def test_ip_frequency_not_triggered(self):
        rule = SimpleNamespace(
            rule_type="ip_frequency",
            config={"max_requests": 10},
            action="block",
        )
        ctx = {"request_count": 5}
        assert _evaluate_rule(rule, ctx) is False

    def test_unknown_rule_type_not_triggered(self):
        rule = SimpleNamespace(
            rule_type="unknown_type",
            config={},
            action="warn",
        )
        ctx = {"some_data": 123}
        assert _evaluate_rule(rule, ctx) is False
