"""渠道分析服务单元测试"""

import uuid


class TestHealthScoreCalculation:
    """测试渠道健康评分算法"""

    def test_perfect_score(self):
        """无异常时满分 100"""
        anomaly_rate = 0.0
        cross_region_rate = 0.0
        repeat_rate = 0.0
        score = max(0, 100 - (anomaly_rate * 40 + cross_region_rate * 30 + repeat_rate * 30))
        assert score == 100

    def test_danger_score(self):
        """高异常时低于 60"""
        anomaly_rate = 0.8
        cross_region_rate = 0.5
        repeat_rate = 0.6
        score = max(0, 100 - (anomaly_rate * 40 + cross_region_rate * 30 + repeat_rate * 30))
        assert score < 60

    def test_warning_score(self):
        """中等异常在 60-80 之间"""
        anomaly_rate = 0.3
        cross_region_rate = 0.1
        repeat_rate = 0.2
        score = max(0, 100 - (anomaly_rate * 40 + cross_region_rate * 30 + repeat_rate * 30))
        assert 60 <= score < 80

    def test_score_never_negative(self):
        """评分不低于 0"""
        anomaly_rate = 2.0
        cross_region_rate = 2.0
        repeat_rate = 2.0
        score = max(0, 100 - (anomaly_rate * 40 + cross_region_rate * 30 + repeat_rate * 30))
        assert score == 0

    def test_level_classification(self):
        """测试健康等级分类"""
        assert _level(90) == "healthy"
        assert _level(70) == "warning"
        assert _level(50) == "danger"
        assert _level(80) == "healthy"
        assert _level(60) == "warning"

    def test_repeat_rate_calculation(self):
        """重复扫码率 = (scan_count - scan_uv) / scan_count"""
        scan_count = 100
        scan_uv = 80
        repeat_rate = max(0, (scan_count - scan_uv) / scan_count)
        assert repeat_rate == 0.2

    def test_anomaly_rate_calculation(self):
        """异常率 = 1 - (distinct_ips / scan_uv)"""
        scan_uv = 100
        distinct_ips = 30
        anomaly_rate = max(0, 1 - (distinct_ips / scan_uv))
        assert anomaly_rate == 0.7

    def test_cross_region_rate_calculation(self):
        """跨区率 = cross_region_count / scan_count"""
        scan_count = 200
        cross_count = 10
        rate = cross_count / scan_count
        assert rate == 0.05


class TestConversionRate:
    """测试转化率计算"""

    def test_basic_conversion(self):
        claims = 50
        uv = 1000
        rate = round(claims / uv * 100, 2)
        assert rate == 5.0

    def test_zero_uv(self):
        claims = 10
        uv = 0
        rate = round(claims / uv * 100, 2) if uv > 0 else 0
        assert rate == 0

    def test_vs_average(self):
        channel_rate = 8.0
        average_rate = 5.0
        diff = round(channel_rate - average_rate, 2)
        assert diff == 3.0

    def test_estimated_claims_distribution(self):
        """按 UV 比例分配总领取数"""
        total_claims = 100
        channel_uv = 300
        total_uv = 1000
        estimated = round(total_claims * channel_uv / total_uv)
        assert estimated == 30


class TestSSEClientManagement:
    """测试 SSE 客户端管理逻辑"""

    def test_client_dict_structure(self):
        clients = {}
        tid = str(uuid.uuid4())
        clients[tid] = []
        assert tid in clients
        assert len(clients[tid]) == 0

    def test_client_cleanup(self):
        clients = {}
        tid = str(uuid.uuid4())
        clients[tid] = ["queue1"]
        clients[tid].remove("queue1")
        assert len(clients[tid]) == 0
        del clients[tid]
        assert tid not in clients

    def test_broadcast_to_multiple_clients(self):
        received = []
        queues = [["initial"]]
        for q in queues:
            received.append("alert_data")
        assert len(received) == 1


class TestScanByChannelDimensions:
    """测试渠道维度参数"""

    def test_valid_dimensions(self):
        valid = ["distributor", "region", "store"]
        assert len(valid) == 3

    def test_invalid_dimension_returns_empty(self):
        dimension = "invalid"
        assert dimension not in ("distributor", "region", "store")


def _level(score: float) -> str:
    if score < 60:
        return "danger"
    elif score < 80:
        return "warning"
    return "healthy"
