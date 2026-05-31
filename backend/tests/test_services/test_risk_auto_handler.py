"""EPIC-13 风控自动触发闭环测试"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.risk import RiskRule


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


@pytest.fixture
def sample_rule(tenant_id):
    return RiskRule(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="IP频率限制",
        rule_type="ip_frequency",
        action="block",
        config={"max_requests": 10},
        enabled=True,
    )


@pytest.fixture
def sample_rule_warn(tenant_id):
    return RiskRule(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="时间窗口限制",
        rule_type="time_window",
        action="warn",
        config={"allowed_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18]},
        enabled=True,
    )


class TestEvaluateRule:
    """测试规则评估逻辑"""

    def test_ip_frequency_triggered(self, sample_rule):
        from app.services.risk_rule import _evaluate_rule

        context = {"request_count": 15}
        assert _evaluate_rule(sample_rule, context) is True

    def test_ip_frequency_not_triggered(self, sample_rule):
        from app.services.risk_rule import _evaluate_rule

        context = {"request_count": 5}
        assert _evaluate_rule(sample_rule, context) is False

    def test_time_window_triggered(self, sample_rule_warn):
        from app.services.risk_rule import _evaluate_rule

        context = {"current_hour": 3}  # 凌晨3点，不在允许范围内
        assert _evaluate_rule(sample_rule_warn, context) is True

    def test_time_window_not_triggered(self, sample_rule_warn):
        from app.services.risk_rule import _evaluate_rule

        context = {"current_hour": 10}  # 上午10点，在允许范围内
        assert _evaluate_rule(sample_rule_warn, context) is False

    def test_region_restriction_triggered(self):
        from app.services.risk_rule import _evaluate_rule

        rule = RiskRule(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            name="地域限制",
            rule_type="region_restriction",
            action="block",
            config={"allowed_regions": ["CN", "HK"]},
            enabled=True,
        )
        context = {"detected_region": "US"}
        assert _evaluate_rule(rule, context) is True

    def test_budget_limit_triggered(self):
        from app.services.risk_rule import _evaluate_rule

        rule = RiskRule(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            name="预算限制",
            rule_type="budget_limit",
            action="block",
            config={"max_budget": 1000},
            enabled=True,
        )
        context = {"current_spend": 1500}
        assert _evaluate_rule(rule, context) is True

    def test_unknown_rule_type_not_triggered(self):
        from app.services.risk_rule import _evaluate_rule

        rule = RiskRule(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            name="未知规则",
            rule_type="unknown_type",
            action="block",
            config={},
            enabled=True,
        )
        context = {}
        assert _evaluate_rule(rule, context) is False


class TestRiskAutoHandlerDedup:
    """测试 Redis 去重逻辑"""

    @pytest.mark.asyncio
    async def test_dedup_first_call_passes(self):
        from app.services.risk_auto_handler import _check_dedup

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.setex = AsyncMock()
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_mod = MagicMock()
        mock_mod.from_url = MagicMock(return_value=mock_redis)

        with patch("redis.asyncio.from_url", mock_mod.from_url):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.redis_url = "redis://localhost:6379"
                result = await _check_dedup("t1", "r1", "p1")
                assert result is False

    @pytest.mark.asyncio
    async def test_dedup_duplicate_blocked(self):
        from app.services.risk_auto_handler import _check_dedup

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_mod = MagicMock()
        mock_mod.from_url = MagicMock(return_value=mock_redis)

        with patch("redis.asyncio.from_url", mock_mod.from_url):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.redis_url = "redis://localhost:6379"
                result = await _check_dedup("t1", "r1", "p1")
                assert result is True


class TestRiskActionExecutor:
    """测试处置动作执行器"""

    @pytest.mark.asyncio
    async def test_execute_warn_creates_alert_and_notification(self, sample_rule_warn):
        from app.services.risk_action import _execute_warn

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()


        mock_code_item = MagicMock()
        mock_code_item.id = uuid.uuid4()

        result = await _execute_warn(
            db=mock_db,
            tenant_id=uuid.uuid4(),
            public_id="ABC123",
            code_item=mock_code_item,
            rule=sample_rule_warn,
            context={"current_hour": 3},
        )

        assert result["steps"][0]["action"] == "create_alert"
        assert result["steps"][0]["status"] == "success"
        # add should be called for alert + notification
        assert mock_db.add.call_count >= 1
