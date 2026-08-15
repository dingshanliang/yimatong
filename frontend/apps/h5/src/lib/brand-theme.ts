/**
 * H5 租户品牌槽位解析（beads: yimatong-z6i0.10）
 *
 * 三层回退：页面/活动 DSL brand_theme → 租户 brand_profile → 一码通默认主题。
 * 主色衍生（hover/active/subtle/onPrimary）与后端 app/utils/brand_color.py 同算法。
 */

export type RadiusPreset = "sm" | "md" | "lg";
export type BackgroundPreset = "canvas" | "muted" | "tinted";

export interface TenantBranding {
  name?: string;
  logo_url?: string;
  primary_color?: string;
  radius_preset?: RadiusPreset;
  background_preset?: BackgroundPreset;
  hide_yimatong_brand?: boolean;
  support_phone?: string;
  support_wecom_url?: string;
}

export interface BrandSlots {
  primaryColor: string;
  radiusPreset: RadiusPreset;
  backgroundPreset: BackgroundPreset;
  hideYimatongBrand: boolean;
  supportPhone: string;
  supportWecomUrl: string;
}

export interface BrandShades {
  primary: string;
  hover: string;
  active: string;
  subtle: string;
  onPrimary: string;
}

/** 默认主色：方向 A 语义层 light color.action.primary */
const DEFAULT_PRIMARY = "#15803d";

/**
 * 平台默认客服联系方式（三层回退的第三层）。
 * 电话可由部署配置 NEXT_PUBLIC_SUPPORT_PHONE_FALLBACK 覆盖；未配置时使用
 * 文档化占位号（docs/02_tech/design-system/h5-branding.md）。企微默认无
 * （不渲染链接）。
 */
export const DEFAULT_SUPPORT_PHONE =
  process.env.NEXT_PUBLIC_SUPPORT_PHONE_FALLBACK || "400-000-0000";
export const DEFAULT_SUPPORT_WECOM_URL = "";

/**
 * 客服电话合法性：与后端 app/utils/brand_color.py._validate_support_phone
 * 同规则——剔除分隔符 " -*()" 后为数字（至多一个前导 +）、长度 5-20。
 */
export function isValidSupportPhone(value: string): boolean {
  const stripped = value.replace(/[ \-*()]/g, "");
  return (
    stripped.length >= 5 && stripped.length <= 20 && /^\+?\d+$/.test(stripped)
  );
}

/** 仅保留 tel: 安全字符，防止注入。 */
export function safeTelHref(phone: string): string {
  return `tel:${phone.replace(/[^0-9+\-*()]/g, "")}`;
}

/** 仅接受 https 链接。 */
export function safeHttpsHref(url: string): string | null {
  return /^https:\/\/[^\s]+$/i.test(url) ? url : null;
}

const HEX_RE = /^#[0-9a-f]{6}$/i;

function parseHex(color: string): [number, number, number] | null {
  if (!HEX_RE.test(color)) return null;
  return [
    parseInt(color.slice(1, 3), 16),
    parseInt(color.slice(3, 5), 16),
    parseInt(color.slice(5, 7), 16),
  ];
}

function toHex(r: number, g: number, b: number): string {
  const c = (v: number) =>
    Math.max(0, Math.min(255, Math.round(v * 255)))
      .toString(16)
      .padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

function rgbToHls(r: number, g: number, b: number): [number, number, number] {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b),
    min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, l, 0];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, l, s];
}

function hlsToRgb(h: number, l: number, s: number): [number, number, number] {
  if (s === 0) return [l, l, l];
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [hue2rgb(p, q, h + 1 / 3), hue2rgb(p, q, h), hue2rgb(p, q, h - 1 / 3)];
}

function shiftLightness(color: string, delta: number): string {
  const rgb = parseHex(color)!;
  const [h, l, s] = rgbToHls(...rgb);
  return toHex(...hlsToRgb(h, Math.max(0, Math.min(1, l + delta)), s));
}

function mixWithWhite(color: string, ratio: number): string {
  const [r, g, b] = parseHex(color)!;
  return toHex(
    r / 255 + (1 - r / 255) * ratio,
    g / 255 + (1 - g / 255) * ratio,
    b / 255 + (1 - b / 255) * ratio
  );
}

function linear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function contrastRatio(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = parseHex(hex)!;
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(bl);
  };
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

export function deriveShades(anchor: string): BrandShades {
  const primary = parseHex(anchor) ? anchor.toLowerCase() : DEFAULT_PRIMARY;
  const black = contrastRatio("#000000", primary);
  const white = contrastRatio("#ffffff", primary);
  return {
    primary,
    hover: shiftLightness(primary, -0.08),
    active: shiftLightness(primary, -0.14),
    subtle: mixWithWhite(primary, 0.94),
    onPrimary: black >= white ? "#000000" : "#ffffff",
  };
}

function readDslTheme(
  pageConfig?: Record<string, unknown>
): Record<string, unknown> {
  if (!pageConfig) return {};
  if (pageConfig.brand_theme && typeof pageConfig.brand_theme === "object") {
    return pageConfig.brand_theme as Record<string, unknown>;
  }
  const page = pageConfig.page;
  if (
    page &&
    typeof page === "object" &&
    typeof (page as Record<string, unknown>).brand_theme === "object"
  ) {
    return (page as Record<string, unknown>).brand_theme as Record<
      string,
      unknown
    >;
  }
  return {};
}

const RADIUS_PRESETS: RadiusPreset[] = ["sm", "md", "lg"];
const BACKGROUND_PRESETS: BackgroundPreset[] = ["canvas", "muted", "tinted"];

/** 三层回退解析：DSL → 租户 profile → 默认。非法值忽略并回落。 */
export function resolveBrandSlots(
  tenant?: TenantBranding,
  pageConfig?: Record<string, unknown>
): BrandSlots {
  const dsl = readDslTheme(pageConfig);

  const pickColor = (...candidates: unknown[]): string => {
    for (const c of candidates) {
      if (typeof c === "string" && parseHex(c)) return c.toLowerCase();
    }
    return DEFAULT_PRIMARY;
  };
  const pickPreset = <T extends string>(
    allowed: readonly T[],
    fallback: T,
    ...candidates: unknown[]
  ): T => {
    for (const c of candidates) {
      if (typeof c === "string" && (allowed as readonly string[]).includes(c))
        return c as T;
    }
    return fallback;
  };
  const pickBool = (...candidates: unknown[]): boolean => {
    for (const c of candidates) {
      if (typeof c === "boolean") return c;
    }
    return false;
  };
  const pickPhone = (...candidates: unknown[]): string => {
    for (const c of candidates) {
      if (typeof c === "string" && isValidSupportPhone(c)) return c;
    }
    return DEFAULT_SUPPORT_PHONE;
  };
  const pickSecureUrl = (...candidates: unknown[]): string => {
    for (const c of candidates) {
      if (typeof c === "string" && safeHttpsHref(c) !== null) return c;
    }
    return DEFAULT_SUPPORT_WECOM_URL;
  };

  return {
    primaryColor: pickColor(dsl.primary_color, tenant?.primary_color),
    radiusPreset: pickPreset(
      RADIUS_PRESETS,
      "md",
      dsl.radius_preset,
      tenant?.radius_preset
    ),
    backgroundPreset: pickPreset(
      BACKGROUND_PRESETS,
      "canvas",
      dsl.background_preset,
      tenant?.background_preset
    ),
    hideYimatongBrand: pickBool(
      dsl.hide_yimatong_brand,
      tenant?.hide_yimatong_brand
    ),
    supportPhone: pickPhone(tenant?.support_phone),
    supportWecomUrl: pickSecureUrl(tenant?.support_wecom_url),
  };
}

const RADIUS_MAP: Record<
  RadiusPreset,
  { sm: number; md: number; lg: number; xl: number }
> = {
  sm: { sm: 4, md: 6, lg: 8, xl: 12 },
  md: { sm: 6, md: 10, lg: 14, xl: 18 },
  lg: { sm: 8, md: 14, lg: 18, xl: 24 },
};

/** 槽位 → CSS 变量映射（注入 H5 根容器，组件只消费变量） */
export function brandCssVars(slots: BrandSlots): Record<string, string> {
  const shades = deriveShades(slots.primaryColor);
  const radius = RADIUS_MAP[slots.radiusPreset];
  const pageBg =
    slots.backgroundPreset === "muted"
      ? "#f0f4ef"
      : slots.backgroundPreset === "tinted"
        ? mixWithWhite(shades.primary, 0.95)
        : "#f6f8f5";

  return {
    "--ymt-color-brand": shades.primary,
    "--ymt-color-brand-subtle": shades.subtle,
    "--ymt-color-action": shades.primary,
    "--ymt-color-action-hover": shades.hover,
    "--ymt-color-action-active": shades.active,
    "--ymt-color-on-action": shades.onPrimary,
    "--ymt-radius-sm": `${radius.sm}px`,
    "--ymt-radius-md": `${radius.md}px`,
    "--ymt-radius-lg": `${radius.lg}px`,
    "--ymt-radius-xl": `${radius.xl}px`,
    "--ymt-brand-page-bg": pageBg,
  };
}
