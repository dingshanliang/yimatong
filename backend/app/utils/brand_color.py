"""租户品牌主色校验与派生（H5 受控定制槽位，beads: yimatong-z6i0.10）

规则（设计体系 h5-branding.md）：
- 主色自由 hex，但与白底对比度 ≥ 3:1，且亮度在安全域内（不过曝、不近黑）
- hover/active/subtle 等衍生色由锚点算法派生，租户不逐项配置
- 按钮文字色自动选黑/白（对比度 ≥ 4.5:1）
"""

from __future__ import annotations

import colorsys

from app.utils.public_url import normalize_public_url

_HEX_LENGTH = 7  # #rrggbb

MIN_CONTRAST_ON_WHITE = 3.0
MIN_LIGHTNESS = 0.12  # 近黑拒绝
MAX_LIGHTNESS = 0.85  # 过浅拒绝（同时兜底对比度）


class BrandColorError(ValueError):
    """品牌主色不合法或不满足安全约束"""


def _parse_hex(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str) or not color.startswith("#") or len(color) != _HEX_LENGTH:
        raise BrandColorError(f"主色必须是 #rrggbb 格式：{color!r}")
    try:
        return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as exc:
        raise BrandColorError(f"主色必须是 #rrggbb 格式：{color!r}") from exc


def _to_hex(red: float, green: float, blue: float) -> str:
    channels = (max(0, min(255, round(c * 255))) for c in (red, green, blue))
    return "#" + "".join(f"{c:02x}" for c in channels)


def _relative_luminance(color: str) -> float:
    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = _parse_hex(color)
    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 对比度（1–21）"""
    first, second = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _lightness(color: str) -> float:
    red, green, blue = _parse_hex(color)
    _, lightness, _ = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    return lightness


def validate_brand_primary_color(color: str) -> str:
    """校验租户品牌主色，返回规范化的小写 hex；不合格抛 BrandColorError"""
    red, green, blue = _parse_hex(color)
    normalized = f"#{red:02x}{green:02x}{blue:02x}"

    ratio = contrast_ratio(normalized, "#ffffff")
    if ratio < MIN_CONTRAST_ON_WHITE:
        raise BrandColorError(
            f"主色与白底对比度 {ratio:.2f}:1，低于门槛 {MIN_CONTRAST_ON_WHITE:.1f}:1，请使用更深的颜色"
        )

    lightness = _lightness(normalized)
    if lightness < MIN_LIGHTNESS:
        raise BrandColorError("主色过暗（接近纯黑），请使用更亮的品牌色")
    if lightness > MAX_LIGHTNESS:
        raise BrandColorError("主色过浅，请使用更深的品牌色")

    return normalized


def _shift_lightness(color: str, delta: float) -> str:
    red, green, blue = _parse_hex(color)
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    new_lightness = max(0.0, min(1.0, lightness + delta))
    r, g, b = colorsys.hls_to_rgb(hue, new_lightness, saturation)
    return _to_hex(r, g, b)


def _mix_with_white(color: str, ratio: float) -> str:
    red, green, blue = _parse_hex(color)
    return _to_hex(
        red / 255 + (1 - red / 255) * ratio,
        green / 255 + (1 - green / 255) * ratio,
        blue / 255 + (1 - blue / 255) * ratio,
    )


def pick_on_primary(background: str, minimum: float = 4.5) -> str:
    """按钮文字色：在黑/白中择优，达到对比度门槛"""
    black_ratio = contrast_ratio("#000000", background)
    white_ratio = contrast_ratio("#ffffff", background)
    if max(black_ratio, white_ratio) < minimum:
        raise BrandColorError(f"主色 {background} 找不到达标的黑/白文字色")
    return "#000000" if black_ratio >= white_ratio else "#ffffff"


def validate_brand_profile(profile: dict) -> dict:
    """校验租户级 brand_profile（写入路径调用），只保留白名单槽位

    五槽位（ADR-0001）：primary_color / radius_preset / background_preset /
    hide_yimatong_brand / logo_url。Logo 走 /files/upload，值是公开 URL。
    """
    allowed = {
        "primary_color",
        "radius_preset",
        "background_preset",
        "hide_yimatong_brand",
        "logo_url",
    }
    unknown = set(profile) - allowed
    if unknown:
        raise BrandColorError(f"brand_profile 包含不支持的槽位：{sorted(unknown)}")

    if profile.get("primary_color"):
        validate_brand_primary_color(profile["primary_color"])
    if "radius_preset" in profile and profile["radius_preset"] not in ("sm", "md", "lg"):
        raise BrandColorError("radius_preset 必须是预设档：sm / md / lg")
    if "background_preset" in profile and profile["background_preset"] not in ("canvas", "muted", "tinted"):
        raise BrandColorError("background_preset 必须是预设组：canvas / muted / tinted")
    if "logo_url" in profile and profile["logo_url"] is not None:
        try:
            profile["logo_url"] = normalize_public_url(profile["logo_url"])
        except (TypeError, ValueError) as exc:
            raise BrandColorError(f"logo_url 不是安全的公开图片地址：{profile['logo_url']!r}") from exc
    return profile


def validate_brand_theme(config: dict) -> dict:
    """校验页面 DSL 中的 brand_theme 槽位（两种形状：顶层或 page.brand_theme）

    在 PageVersion 保存路径调用；不合法抛 BrandColorError（经 pydantic 转为 422）。
    """
    theme = config.get("brand_theme")
    if theme is None and isinstance(config.get("page"), dict):
        theme = config["page"].get("brand_theme")
    if not isinstance(theme, dict):
        return config

    primary_color = theme.get("primary_color")
    if primary_color:
        validate_brand_primary_color(primary_color)
    return config


def derive_brand_shades(anchor: str) -> dict[str, str]:
    """从主色锚点派生完整槽位：hover/active/subtle/onPrimary"""
    red, green, blue = _parse_hex(anchor)
    primary = f"#{red:02x}{green:02x}{blue:02x}"
    black_ratio = contrast_ratio("#000000", primary)
    white_ratio = contrast_ratio("#ffffff", primary)
    on_primary = "#000000" if black_ratio >= white_ratio else "#ffffff"
    return {
        "primary": primary,
        "hover": _shift_lightness(primary, -0.08),
        "active": _shift_lightness(primary, -0.14),
        "subtle": _mix_with_white(primary, 0.94),
        "onPrimary": on_primary,
    }
