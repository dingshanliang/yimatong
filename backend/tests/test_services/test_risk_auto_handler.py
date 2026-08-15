"""EPIC-13 风控自动触发闭环测试"""

import uuid
from unittest.mock import AsyncMock, patch

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


class TestDiversionObservationOwnership:
    def test_non_resolver_events_keep_handler_fallback(self):
        from app.services.risk_auto_handler import _event_handler_owns_diversion_observation

        assert _event_handler_owns_diversion_observation({}) is True
        assert _event_handler_owns_diversion_observation({"diversion_observation_owner": "risk_auto_handler"}) is True

    def test_resolver_event_uses_same_transaction_authority_only(self):
        from app.services.risk_auto_handler import _event_handler_owns_diversion_observation

        assert _event_handler_owns_diversion_observation({"diversion_observation_owner": "resolver"}) is False


class TestRiskActionExecutor:
    """DB authority owns deduplication and all action side effects."""

    @pytest.mark.asyncio
    async def test_execute_uses_exact_scan_rule_and_deterministic_receipt(
        self,
        sample_rule,
        tenant_id,
    ):
        from app.services.risk_action import execute_risk_action

        mock_db = AsyncMock()
        scan_event_id = uuid.uuid4()
        result = {"triggered": True, "action": "block", "replayed": False}
        authority = AsyncMock(return_value=result)

        with patch("app.services.risk_action.evaluate_execute_scan", authority):
            first = await execute_risk_action(
                mock_db,
                tenant_id,
                scan_event_id=scan_event_id,
                rule=sample_rule,
                context={"request_count": 20},
                idempotency_key=f"scan-risk:{scan_event_id}:{sample_rule.id}",
            )
            second = await execute_risk_action(
                mock_db,
                tenant_id,
                scan_event_id=scan_event_id,
                rule=sample_rule,
                context={"request_count": 20},
                idempotency_key=f"scan-risk:{scan_event_id}:{sample_rule.id}",
            )

        assert first == second == result
        assert authority.await_count == 2
        first_kwargs = authority.await_args_list[0].kwargs
        second_kwargs = authority.await_args_list[1].kwargs
        assert first_kwargs["scan_event_id"] == scan_event_id
        assert first_kwargs["rule_id"] == sample_rule.id
        assert first_kwargs["receipt_id"] == second_kwargs["receipt_id"]

    @pytest.mark.asyncio
    async def test_execute_propagates_authority_failure_without_fallback(self, sample_rule, tenant_id):
        from app.services.risk_action import execute_risk_action

        mock_db = AsyncMock()
        authority = AsyncMock(side_effect=RuntimeError("authority unavailable"))
        with patch("app.services.risk_action.evaluate_execute_scan", authority):
            with pytest.raises(RuntimeError, match="authority unavailable"):
                await execute_risk_action(
                    mock_db,
                    tenant_id,
                    scan_event_id=uuid.uuid4(),
                    rule=sample_rule,
                    context={"request_count": 20},
                    idempotency_key="scan-risk:test",
                )
        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_awaited()
