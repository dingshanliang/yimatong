"""H5 租户品牌主色校验与派生测试（beads: yimatong-z6i0.10）"""

import pytest

from app.utils.brand_color import (
    BrandColorError,
    contrast_ratio,
    derive_brand_shades,
    validate_brand_primary_color,
    validate_brand_profile,
    validate_brand_theme,
)


class TestContrastRatio:
    def test_black_on_white_is_21(self):
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)

    def test_same_color_is_1(self):
        assert contrast_ratio("#16a34a", "#16a34a") == pytest.approx(1.0, abs=0.01)

    def test_rejects_malformed_hex(self):
        with pytest.raises(BrandColorError):
            contrast_ratio("#abc", "#ffffff")


class TestValidateBrandPrimaryColor:
    @pytest.mark.parametrize("color", ["#16a34a", "#1F7A4D", "#7c3aed", "#b45309", "#0f766e"])
    def test_accepts_safe_brand_colors(self, color: str):
        assert validate_brand_primary_color(color) == color.lower()

    def test_normalizes_uppercase(self):
        assert validate_brand_primary_color("#16A34A") == "#16a34a"

    @pytest.mark.parametrize(
        "color",
        [
            "#ffffff",  # 纯白：对比度 1:1
            "#f8fafc",  # 近白
            "#fde047",  # 亮黄：对白底对比度不足 3:1
            "#a3e635",  # 荧光绿
        ],
    )
    def test_rejects_low_contrast_colors(self, color: str):
        with pytest.raises(BrandColorError, match="对比度"):
            validate_brand_primary_color(color)

    @pytest.mark.parametrize("color", ["#000000", "#111111"])
    def test_rejects_near_black(self, color: str):
        """近黑色亮度低于安全域下限（观感与可用性）"""
        with pytest.raises(BrandColorError):
            validate_brand_primary_color(color)

    @pytest.mark.parametrize("color", ["red", "#12345", "16a34a", "#gggggg", ""])
    def test_rejects_malformed(self, color: str):
        with pytest.raises(BrandColorError):
            validate_brand_primary_color(color)


class TestDeriveBrandShades:
    def test_hover_is_darker_than_anchor(self):
        shades = derive_brand_shades("#16a34a")
        assert shades["primary"] == "#16a34a"
        assert contrast_ratio(shades["hover"], "#ffffff") > contrast_ratio("#16a34a", "#ffffff")

    def test_active_darker_than_hover(self):
        shades = derive_brand_shades("#16a34a")
        assert contrast_ratio(shades["active"], "#ffffff") >= contrast_ratio(shades["hover"], "#ffffff")

    def test_subtle_bg_is_light_and_readable_with_primary(self):
        shades = derive_brand_shades("#16a34a")
        # subtle 背景应足够浅，能承载 primary 色文字（≥3:1）
        assert contrast_ratio(shades["primary"], shades["subtle"]) >= 3.0

    def test_returns_all_slots(self):
        shades = derive_brand_shades("#1F7A4D")
        assert set(shades) == {"primary", "hover", "active", "subtle", "onPrimary"}
        for value in shades.values():
            assert value.startswith("#") and len(value) == 7

    def test_on_primary_picks_readable_foreground(self):
        # #16a34a 亮度中等：白字仅 3.3:1 不达标，黑字 6.3:1 胜出
        assert derive_brand_shades("#16a34a")["onPrimary"] == "#000000"
        # 更深的绿色白字达标
        assert derive_brand_shades("#15803d")["onPrimary"] == "#ffffff"
        assert derive_brand_shades("#f59e0b")["onPrimary"] == "#000000"


class TestValidateBrandTheme:
    def test_passes_without_brand_theme(self):
        assert validate_brand_theme({"modules": []}) == {"modules": []}

    def test_validates_top_level_brand_theme(self):
        config = {"brand_theme": {"primary_color": "#1F7A4D"}}
        assert validate_brand_theme(config) is config

    def test_validates_nested_page_brand_theme(self):
        config = {"page": {"brand_theme": {"primary_color": "#1F7A4D"}}}
        assert validate_brand_theme(config) is config

    def test_rejects_unsafe_color_in_dsl(self):
        with pytest.raises(BrandColorError):
            validate_brand_theme({"brand_theme": {"primary_color": "#fde047"}})
        with pytest.raises(BrandColorError):
            validate_brand_theme({"page": {"brand_theme": {"primary_color": "red"}}})


class TestValidateBrandProfile:
    def test_accepts_full_profile(self):
        profile = {
            "primary_color": "#1F7A4D",
            "radius_preset": "md",
            "background_preset": "tinted",
            "hide_yimatong_brand": True,
        }
        assert validate_brand_profile(profile) is profile

    def test_rejects_unknown_slots(self):
        with pytest.raises(BrandColorError, match="不支持的槽位"):
            validate_brand_profile({"font_family": "serif", "custom_css": "body{}"})

    def test_rejects_bad_presets(self):
        with pytest.raises(BrandColorError):
            validate_brand_profile({"radius_preset": "huge"})
        with pytest.raises(BrandColorError):
            validate_brand_profile({"background_preset": "dark"})

    def test_rejects_unsafe_primary_color(self):
        with pytest.raises(BrandColorError):
            validate_brand_profile({"primary_color": "#fde047"})

    # -- logo_url 槽位（ADR-0001：Logo 并入 brand_profile，复用 /files/upload）--

    def test_accepts_logo_url(self):
        profile = {"logo_url": "https://cdn.example.com/t/logo.png"}
        assert validate_brand_profile(profile) is profile

    def test_accepts_logo_url_with_full_profile(self):
        profile = {
            "primary_color": "#1F7A4D",
            "radius_preset": "md",
            "background_preset": "tinted",
            "hide_yimatong_brand": True,
            "logo_url": "https://cdn.example.com/t/logo.png",
        }
        assert validate_brand_profile(profile) is profile

    def test_accepts_logo_url_relative_internal(self):
        """内部 /files/public 路径也允许（/files/upload 返回的相对路径）"""
        profile = {"logo_url": "/files/public/demo/brand-logo/abc.png"}
        assert validate_brand_profile(profile) is profile

    def test_rejects_logo_url_non_url(self):
        with pytest.raises(BrandColorError, match="logo_url"):
            validate_brand_profile({"logo_url": "not a url"})

    def test_rejects_logo_url_javascript_scheme(self):
        with pytest.raises(BrandColorError, match="logo_url"):
            validate_brand_profile({"logo_url": "javascript:alert(1)"})

    def test_logo_url_optional(self):
        """未提供 logo_url 时仍合法"""
        assert validate_brand_profile({"primary_color": "#1F7A4D"}) == {"primary_color": "#1F7A4D"}
