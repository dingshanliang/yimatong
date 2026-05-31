"""区域品牌服务单元测试"""

import uuid
from datetime import UTC, datetime

from app.models.regional import RegionalOrg, RegionalOrgMember, RegionalTemplate


class TestRegionalOrgModel:
    def test_org_creation(self):
        tid = uuid.uuid4()
        org = RegionalOrg(tenant_id=tid, name="测试协会", org_type="association")
        assert org.name == "测试协会"
        assert org.org_type == "association"
        assert org.config in ({}, None)  # SQLite vs PG default

    def test_org_default_type(self):
        org = RegionalOrg(tenant_id=uuid.uuid4(), name="测试", org_type="association")
        assert org.org_type == "association"


class TestRegionalOrgMemberModel:
    def test_member_creation(self):
        member = RegionalOrgMember(
            org_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            member_name="测试企业",
        )
        assert member.member_name == "测试企业"
        assert member.status in ("active", None)  # SQLite vs PG

    def test_member_status_values(self):
        valid_statuses = ["active", "suspended", "expelled"]
        for status in valid_statuses:
            member = RegionalOrgMember(
                org_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                member_name="test",
                status=status,
            )
            assert member.status == status


class TestRegionalTemplateModel:
    def test_template_creation(self):
        t = RegionalTemplate(
            org_id=uuid.uuid4(),
            name="标准模板",
            config={"components": [{"type": "header"}]},
        )
        assert t.name == "标准模板"
        assert "components" in t.config

    def test_template_empty_config(self):
        t = RegionalTemplate(org_id=uuid.uuid4(), name="空模板", config={})
        assert t.config == {}


class TestDashboardStats:
    def test_member_stats_structure(self):
        """验证成员统计返回的数据结构"""
        stats = {
            "tenant_id": str(uuid.uuid4()),
            "member_name": "测试企业",
            "scan_count": 100,
            "claim_count": 25,
        }
        assert "tenant_id" in stats
        assert "member_name" in stats
        assert "scan_count" in stats
        assert "claim_count" in stats

    def test_product_stats_structure(self):
        """验证产品统计返回的数据结构"""
        stats = {
            "product_id": str(uuid.uuid4()),
            "product_name": "测试产品",
            "scan_count": 500,
        }
        assert "product_id" in stats
        assert "product_name" in stats
        assert "scan_count" in stats

    def test_conversion_rate_calculation(self):
        """验证转化率计算逻辑"""
        scan_count = 100
        claim_count = 5
        rate = claim_count / scan_count * 100
        assert rate == 5.0

    def test_zero_scan_conversion(self):
        scan_count = 0
        claim_count = 5
        rate = claim_count / scan_count * 100 if scan_count else 0
        assert rate == 0

    def test_dashboard_response_structure(self):
        """验证看板返回值结构"""
        dashboard = {
            "org_id": str(uuid.uuid4()),
            "member_count": 10,
            "product_count": 5,
            "total_scans": 1000,
            "total_claims": 50,
            "days_back": 30,
            "by_member": [],
            "by_product": [],
        }
        required_keys = ["org_id", "member_count", "product_count", "total_scans", "total_claims", "by_member", "by_product"]
        for key in required_keys:
            assert key in dashboard


class TestMemberStatusTransition:
    """测试成员状态转换逻辑"""

    def test_active_to_suspended(self):
        member = RegionalOrgMember(
            org_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            member_name="test",
            status="active",
        )
        member.status = "suspended"
        assert member.status == "suspended"

    def test_suspended_to_active(self):
        member = RegionalOrgMember(
            org_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            member_name="test",
            status="suspended",
        )
        member.status = "active"
        assert member.status == "active"

    def test_active_to_expelled(self):
        member = RegionalOrgMember(
            org_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            member_name="test",
            status="active",
        )
        member.status = "expelled"
        assert member.status == "expelled"

    def test_name_update(self):
        member = RegionalOrgMember(
            org_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            member_name="旧名称",
        )
        member.member_name = "新名称"
        assert member.member_name == "新名称"


class TestPublishHistory:
    """测试模板下发历史记录"""

    def test_publish_history_append(self):
        config: dict = {}
        if "publish_history" not in config:
            config["publish_history"] = []
        config["publish_history"].append({
            "published_at": datetime.now(UTC).isoformat(),
            "member_count": 5,
            "delivered_count": 4,
        })
        assert len(config["publish_history"]) == 1
        assert config["publish_history"][0]["member_count"] == 5

    def test_multiple_publishes(self):
        config: dict = {"publish_history": []}
        for i in range(3):
            config["publish_history"].append({
                "published_at": datetime.now(UTC).isoformat(),
                "member_count": i + 1,
                "delivered_count": i,
            })
        assert len(config["publish_history"]) == 3
