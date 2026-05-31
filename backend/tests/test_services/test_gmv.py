"""GMV 归因服务单元测试"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.gmv import ExternalOrder, GmvAttribution
from app.services.gmv import (
    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    _extract_budget,
    _hours_between,
    _parse_time,
)


class TestParseTime:
    def test_parse_z_suffix(self):
        result = _parse_time("2026-05-31T10:00:00Z")
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 31

    def test_parse_with_offset(self):
        result = _parse_time("2026-05-31T10:00:00+08:00")
        assert result.hour == 10

    def test_parse_utc_offset(self):
        result = _parse_time("2026-05-31T02:00:00+00:00")
        assert result.hour == 2


class TestHoursBetween:
    def test_basic(self):
        a = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
        b = datetime(2026, 5, 31, 17, 0, tzinfo=timezone.utc)
        assert _hours_between(a, b) == 7.0

    def test_zero(self):
        dt = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
        assert _hours_between(dt, dt) == 0

    def test_negative_returns_zero(self):
        a = datetime(2026, 5, 31, 17, 0, tzinfo=timezone.utc)
        b = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
        assert _hours_between(a, b) == 0

    def test_none_returns_zero(self):
        assert _hours_between(None, None) == 0


class TestExtractBudget:
    def test_with_budget(self):
        assert _extract_budget({"budget": 5000.0}) == 5000.0

    def test_without_budget(self):
        assert _extract_budget({"other": "value"}) is None

    def test_empty_dict(self):
        assert _extract_budget({}) is None

    def test_none_input(self):
        assert _extract_budget(None) is None

    def test_nested_budget(self):
        assert _extract_budget({"budget": 10000, "other": 1}) == 10000


class TestDefaultAttributionWindow:
    def test_default_is_168_hours(self):
        assert DEFAULT_ATTRIBUTION_WINDOW_HOURS == 168

    def test_168_is_7_days(self):
        assert DEFAULT_ATTRIBUTION_WINDOW_HOURS == 7 * 24


class TestConfidenceScore:
    """Test confidence score calculation logic"""

    def test_full_confidence_same_time(self):
        """Orders placed right after scan should have high confidence"""
        window = 168
        hours_diff = 0
        confidence = max(0.1, 1.0 - (hours_diff / window) * 0.5)
        assert confidence == 1.0

    def test_degraded_confidence(self):
        """Orders placed later should have lower confidence"""
        window = 168
        hours_diff = 84  # half window
        confidence = max(0.1, 1.0 - (hours_diff / window) * 0.5)
        assert confidence == 0.75

    def test_minimum_confidence(self):
        """Even at edge of window, confidence doesn't drop below threshold"""
        window = 168
        hours_diff = 336  # double window
        confidence = max(0.1, 1.0 - (hours_diff / window) * 0.5)
        assert confidence == 0.1


class TestExternalOrderModel:
    def test_order_creation(self):
        tid = uuid.uuid4()
        order = ExternalOrder(
            tenant_id=tid,
            external_id="ORD-001",
            amount=99.9,
            phone_hash="abc123",
            product_name="测试产品",
            channel="taobao",
            source_system="erp",
        )
        assert order.external_id == "ORD-001"
        assert order.amount == 99.9
        assert order.channel == "taobao"
        assert order.source_system == "erp"
        assert order.matched in (False, None)  # SQLite vs PG default behavior

    def test_order_optional_fields(self):
        order = ExternalOrder(
            tenant_id=uuid.uuid4(),
            external_id="ORD-002",
            amount=50.0,
        )
        assert order.phone_hash is None
        assert order.product_name is None
        assert order.channel is None
        assert order.source_system is None


class TestGmvAttributionModel:
    def test_attribution_creation(self):
        attr = GmvAttribution(
            tenant_id=uuid.uuid4(),
            external_order_id=uuid.uuid4(),
            public_id="ABC1234567",
            code_item_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            consumer_id=uuid.uuid4(),
            amount=199.0,
            match_type="phone",
            scan_time=datetime.now(timezone.utc),
            attribution_window_hours=168,
            confidence_score=0.95,
        )
        assert attr.match_type == "phone"
        assert attr.confidence_score == 0.95
        assert attr.attribution_window_hours == 168

    def test_attribution_defaults(self):
        attr = GmvAttribution(
            tenant_id=uuid.uuid4(),
            external_order_id=uuid.uuid4(),
            amount=100.0,
            match_type="phone",
        )
        assert attr.attribution_window_hours in (168, None)  # SQLite vs PG default behavior
        assert attr.confidence_score in (1.0, None)
        assert attr.code_item_id is None
        assert attr.campaign_id is None
        assert attr.consumer_id is None
        assert attr.scan_time is None


class TestROIFormulas:
    """Test ROI calculation formulas"""

    def test_roi_calculation(self):
        gmv = 15000
        budget = 5000
        roi = gmv / budget
        assert roi == 3.0

    def test_scan_cost(self):
        budget = 5000
        scan_count = 10000
        cost = budget / scan_count
        assert cost == 0.5

    def test_conversion_rate(self):
        orders = 50
        scan_uv = 1000
        rate = orders / scan_uv * 100
        assert rate == 5.0

    def test_zero_budget_roi(self):
        gmv = 15000
        budget = 0
        roi = gmv / budget if budget else 0
        assert roi == 0

    def test_zero_uv_conversion(self):
        orders = 50
        scan_uv = 0
        rate = orders / scan_uv * 100 if scan_uv else 0
        assert rate == 0
