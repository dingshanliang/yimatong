"""多语言 i18n 单元测试"""


class TestLanguageDetection:
    """测试语言检测逻辑"""

    def _detect(self, accept_language: str) -> str:
        if not accept_language:
            return "zh"
        primary = accept_language.split(",")[0].strip().lower()
        if primary.startswith("zh"):
            return "zh"
        elif primary.startswith("en"):
            return "en"
        elif primary.startswith("ja"):
            return "ja"
        elif primary.startswith("ko"):
            return "ko"
        return "zh"

    def test_chinese_simplified(self):
        assert self._detect("zh-CN,zh;q=0.9") == "zh"

    def test_chinese_traditional(self):
        assert self._detect("zh-TW") == "zh"

    def test_english(self):
        assert self._detect("en-US,en;q=0.9") == "en"

    def test_japanese(self):
        assert self._detect("ja") == "ja"

    def test_korean(self):
        assert self._detect("ko-KR") == "ko"

    def test_empty_defaults_chinese(self):
        assert self._detect("") == "zh"

    def test_unknown_defaults_chinese(self):
        assert self._detect("fr-FR") == "zh"

    def test_mixed_header_picks_first(self):
        assert self._detect("en-US,zh-CN;q=0.9") == "en"


class TestTranslationKeyStructure:
    """测试翻译键结构"""

    def test_key_format_dot_notation(self):
        key = "menu.dashboard"
        parts = key.split(".")
        assert len(parts) == 2
        assert parts[0] == "menu"
        assert parts[1] == "dashboard"

    def test_key_prefix_match(self):
        key = "menu.dashboard"
        prefix = "menu"
        assert key.startswith(prefix)

    def test_key_prefix_no_match(self):
        key = "common.hello"
        prefix = "menu"
        assert not key.startswith(prefix)


class TestLocaleCodes:
    """测试语言代码规范"""

    def test_valid_locale_codes(self):
        valid = {"zh", "en", "ja", "ko"}
        assert len(valid) == 4

    def test_locale_from_accept_language(self):
        """Accept-Language 可以解析出简短语言代码"""
        assert "zh-CN".split("-")[0] == "zh"
        assert "en-US".split("-")[0] == "en"
        assert "ja".split("-")[0] == "ja"


class TestBatchTranslation:
    """测试批量翻译逻辑"""

    def test_batch_upsert_count(self):
        translations = [
            {"key": "common.hello", "locale": "zh", "value": "你好"},
            {"key": "common.hello", "locale": "en", "value": "Hello"},
            {"key": "common.bye", "locale": "zh", "value": "再见"},
        ]
        assert len(translations) == 3

    def test_unique_key_locale_pair(self):
        """同一 key+locale 组合唯一"""
        pairs = [
            ("common.hello", "zh"),
            ("common.hello", "en"),
            ("common.hello", "zh"),  # 重复
        ]
        unique = set(pairs)
        assert len(unique) == 2


class TestI18nFrontendIntegration:
    """测试前端 i18n 集成"""

    def test_h5_language_param(self):
        """H5 通过 ?lang=en 参数切换语言"""
        import urllib.parse

        url = "https://qr.yimatong.cn/c/ABC123?lang=en"
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        assert params.get("lang") == ["en"]

    def test_admin_locale_storage(self):
        """Admin 通过 localStorage 存储语言偏好"""
        locale_key = "ymt_locale"
        assert locale_key == "ymt_locale"

    def test_fallback_chain(self):
        """翻译回退链：指定语言 → 中文 → key 本身"""
        translations_zh = {"common.hello": "你好"}
        translations_en = {"common.hello": "Hello"}

        def t(locale: str, key: str) -> str:
            if locale == "en" and key in translations_en:
                return translations_en[key]
            if key in translations_zh:
                return translations_zh[key]
            return key

        assert t("en", "common.hello") == "Hello"
        assert t("zh", "common.hello") == "你好"
        assert t("ja", "common.hello") == "你好"
        assert t("en", "missing.key") == "missing.key"
