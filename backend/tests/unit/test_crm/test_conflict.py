"""CRM 冲突处理单元测试"""

import pytest

from app.services.crm.conflict import (
    _parse_tags,
    resolve_consumer_conflict,
)


class TestParseTags:
    def test_empty_string(self):
        assert _parse_tags("") == set()
        assert _parse_tags(None) == set()

    def test_single_tag(self):
        assert _parse_tags("VIP") == {"VIP"}

    def test_multiple_tags(self):
        assert _parse_tags("VIP,活跃,高频") == {"VIP", "活跃", "高频"}

    def test_strips_whitespace(self):
        assert _parse_tags(" VIP , 活跃 ") == {"VIP", "活跃"}

    def test_skips_empty(self):
        assert _parse_tags("VIP,,活跃,") == {"VIP", "活跃"}


class TestResolveConflict:
    def test_no_conflict(self):
        result = resolve_consumer_conflict(
            {"nickname": "张三", "member_level": "gold"},
            {"nickname": "张三", "member_level": "gold"},
        )
        assert result.updates == {}
        assert result.conflicts == []

    def test_local_priority_fields(self):
        """手机号、昵称、会员等级以一码通为准"""
        result = resolve_consumer_conflict(
            {"nickname": "本地名", "member_level": "gold"},
            {"nickname": "CRM名", "member_level": "silver"},
        )
        assert result.updates == {}
        assert len(result.conflicts) == 2

    def test_remote_custom_fields(self):
        """crm_ 前缀字段以 CRM 为准"""
        result = resolve_consumer_conflict(
            {"crm_source": "旧来源"},
            {"crm_source": "新来源"},
        )
        assert result.updates.get("crm_source") == "新来源"

    def test_tag_merge_union(self):
        """标签取并集"""
        result = resolve_consumer_conflict(
            {"tags": "VIP,活跃"},
            {"tags": "高频,活跃"},
        )
        assert "tags" in result.updates
        tags = set(result.updates["tags"].split(","))
        assert tags == {"VIP", "活跃", "高频"}
        assert result.tag_merge_details is not None
        assert result.tag_merge_details["added_from_remote"] == ["高频"]
        assert result.tag_merge_details["added_from_local"] == ["VIP"]

    def test_local_null_remote_has_value(self):
        """本地为空时取远程值"""
        result = resolve_consumer_conflict(
            {"nickname": None},
            {"nickname": "CRM名"},
        )
        assert result.updates.get("nickname") == "CRM名"

    def test_new_remote_field(self):
        """远程有新字段本地没有"""
        result = resolve_consumer_conflict(
            {},
            {"crm_external_id": "ext_123"},
        )
        assert result.updates.get("crm_external_id") == "ext_123"
