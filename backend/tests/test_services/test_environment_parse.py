"""A6-006: 环境解析测试"""

from app.services.scan_event import parse_environment


class TestEnvironmentParse:
    def test_wechat(self):
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) MicroMessenger/8.0.38"
        assert parse_environment(ua) == "wechat"

    def test_alipay(self):
        ua = "Mozilla/5.0 AlipayClient/10.3.0"
        assert parse_environment(ua) == "alipay"

    def test_browser(self):
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Safari/604.1"
        assert parse_environment(ua) == "browser"

    def test_none_user_agent(self):
        assert parse_environment(None) == "browser"

    def test_empty_user_agent(self):
        assert parse_environment("") == "browser"

    def test_android_wechat(self):
        ua = "Mozilla/5.0 (Linux; Android 13) MicroMessenger/8.0.38"
        assert parse_environment(ua) == "wechat"
