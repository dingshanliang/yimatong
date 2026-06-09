"""渠道检测核心逻辑单元测试"""

import uuid

import pytest

from app.models.channel import Region
from app.services.channel import _region_matches_detected_city


class TestRegionMatchesDetectedCity:
    """测试 _region_matches_detected_city 精确匹配"""

    def test_exact_city_match(self):
        """城市精确匹配"""
        region = Region(city="上海", province="上海", coverage_type="city", coverage_areas=[{"province": "上海", "city": "上海"}])
        assert _region_matches_detected_city(region, "上海") is True

    def test_exact_city_no_match(self):
        """城市不匹配"""
        region = Region(city="上海", province="上海", coverage_type="city", coverage_areas=[{"province": "上海", "city": "上海"}])
        assert _region_matches_detected_city(region, "北京") is False

    def test_coverage_area_match(self):
        """coverage_areas 匹配"""
        region = Region(city=None, province=None, coverage_type="multi_province", coverage_areas=[{"province": "浙江", "city": "杭州"}, {"province": "江苏", "city": "南京"}])
        assert _region_matches_detected_city(region, "杭州") is True
        assert _region_matches_detected_city(region, "南京") is True
        assert _region_matches_detected_city(region, "上海") is False

    def test_no_false_substring_match(self):
        """city 字段使用 == 精确匹配，不应误匹配子串——'南京'不应匹配'南京路'"""
        region = Region(city="南京路", province="上海", coverage_type="city", coverage_areas=[])
        # "南京" 是 "南京路" 的子串，但精确匹配下不应命中
        assert _region_matches_detected_city(region, "南京") is False

    def test_province_match_fallback(self):
        """当 city 不匹配时，province 匹配仍可命中"""
        region = Region(city="上海浦东", province="上海", coverage_type="city", coverage_areas=[])
        # "上海" 匹配 province="上海"
        assert _region_matches_detected_city(region, "上海") is True
