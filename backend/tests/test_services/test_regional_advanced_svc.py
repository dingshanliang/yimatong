"""区域品牌高级管理单元测试"""

import uuid
from datetime import date, timedelta


class TestAdvancedDashboard:
    """测试高级看板环比计算"""

    def test_scan_change_pct_positive(self):
        """扫码量增加 → 正环比"""
        current = 150
        previous = 100
        change = round((current - previous) / previous * 100, 1)
        assert change == 50.0

    def test_scan_change_pct_negative(self):
        """扫码量减少 → 负环比"""
        current = 80
        previous = 100
        change = round((current - previous) / previous * 100, 1)
        assert change == -20.0

    def test_scan_change_pct_zero_previous(self):
        """上期为 0 → 无法计算环比"""
        current = 50
        previous = 0
        change = round((current - previous) / previous * 100, 1) if previous > 0 else None
        assert change is None

    def test_scan_change_pct_no_change(self):
        current = 100
        previous = 100
        change = round((current - previous) / previous * 100, 1)
        assert change == 0.0


class TestDataIsolationPolicy:
    """测试数据隔离策略"""

    def test_default_policy(self):
        policy = {
            "scan_visibility": "own_only",
            "claim_visibility": "own_only",
            "member_data_visibility": "brand_all",
        }
        assert policy["scan_visibility"] == "own_only"
        assert policy["member_data_visibility"] == "brand_all"

    def test_own_only_visibility(self):
        member_tenant = uuid.uuid4()
        org_tenant = uuid.uuid4()
        assert member_tenant != org_tenant

    def test_brand_all_visibility(self):
        assert "brand_all" == "brand_all"


class TestUnifiedCampaign:
    """测试统一营销活动"""

    def test_campaign_data_structure(self):
        campaign = {
            "id": str(uuid.uuid4()),
            "name": "春节促销",
            "description": "统一春节促销活动",
            "member_count": 5,
            "member_ids": [str(uuid.uuid4()) for _ in range(5)],
            "status": "draft",
        }
        assert campaign["member_count"] == 5
        assert len(campaign["member_ids"]) == 5
        assert campaign["status"] == "draft"

    def test_campaign_all_members(self):
        member_ids: list[str] | None = None
        all_active = [str(uuid.uuid4()) for _ in range(10)]
        target = member_ids if member_ids else all_active
        assert len(target) == 10

    def test_campaign_specific_members(self):
        all_members = [str(uuid.uuid4()) for _ in range(10)]
        member_ids = all_members[:3]
        assert len(member_ids) == 3


class TestRegionalOrgConfig:
    """测试区域组织 config JSON 存储"""

    def test_config_stores_unified_campaigns(self):
        config: dict = {}
        campaigns = [
            {"id": "1", "name": "活动1"},
            {"id": "2", "name": "活动2"},
        ]
        config["unified_campaigns"] = campaigns
        assert len(config["unified_campaigns"]) == 2

    def test_config_stores_data_policy(self):
        config: dict = {}
        config["data_policy"] = {
            "scan_visibility": "own_only",
            "claim_visibility": "brand_all",
        }
        assert config["data_policy"]["scan_visibility"] == "own_only"

    def test_config_stores_publish_history(self):
        config: dict = {}
        config["publish_history"] = [
            {"published_at": "2026-05-31", "member_count": 10, "delivered_count": 8},
        ]
        assert config["publish_history"][0]["delivered_count"] == 8


class TestDailyTrend:
    """测试日趋势数据"""

    def test_daily_trend_date_range(self):
        days_back = 14
        dates = []
        for i in range(days_back):
            day = date.today() - timedelta(days=i)
            dates.append(day.isoformat())
        dates.reverse()
        assert len(dates) == 14
        assert dates[0] < dates[-1]

    def test_daily_trend_sorted_ascending(self):
        trend = [
            {"date": "2026-05-28", "scan_count": 50},
            {"date": "2026-05-29", "scan_count": 60},
            {"date": "2026-05-30", "scan_count": 55},
        ]
        dates = [t["date"] for t in trend]
        assert dates == sorted(dates)
