"""GeoIP 服务单元测试"""

import pytest

from app.services.geoip import _fallback_lookup, resolve_ip_to_city


class TestFallbackLookup:
    """测试 CIDR 降级映射"""

    @pytest.mark.parametrize(
        "ip,expected_city",
        [
            ("110.1.2.3", "北京"),
            ("112.50.100.1", "北京"),
            ("120.0.0.1", "上海"),
            ("121.255.255.255", "上海"),
            ("113.10.20.30", "广东"),
            ("119.100.200.1", "广东"),
            ("114.1.1.1", "湖北"),
            ("202.50.100.1", "四川"),
            ("221.1.1.1", "辽宁"),
            ("1.2.3.4", None),  # 不在映射表中
            ("192.168.1.1", None),  # 私有地址
            ("invalid", None),  # 无效格式
        ],
    )
    def test_fallback_lookup(self, ip, expected_city):
        result = _fallback_lookup(ip)
        assert result == expected_city


class TestResolveIpToCity:
    """测试 IP 到城市解析"""

    def test_returns_city_for_known_fallback_ip(self):
        """已知 IP 应返回对应城市"""
        result = resolve_ip_to_city("110.1.2.3")
        assert result == "北京"

    def test_returns_unknown_for_unmapped_ip(self):
        """未映射 IP 应返回 '未知位置'"""
        result = resolve_ip_to_city("1.2.3.4")
        assert result == "未知位置"

    def test_returns_unknown_for_invalid_ip(self):
        """无效 IP 应返回 '未知位置'"""
        result = resolve_ip_to_city("not-an-ip")
        assert result == "未知位置"
