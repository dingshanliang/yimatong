/**
 * 品牌主色前端预校验（决策 4：前端预校验 + 禁用保存）
 *
 * 与后端 app/utils/brand_color.py 同算法：对比度 ≥ 3:1、亮度在安全域内。
 * 复用 @yimatong/design-tokens 的 contrastRatio，避免与后端校验漂移。
 */

import { contrastRatio } from "@yimatong/design-tokens";

const MIN_CONTRAST_ON_WHITE = 3.0;
const MIN_LIGHTNESS = 0.12; // 近黑拒绝
const MAX_LIGHTNESS = 0.85; // 过浅拒绝
const HEX_RE = /^#[0-9a-f]{6}$/i;

/**
 * 感知亮度（HSL 几何平均 (max+min)/2）。
 * 注意：与 contrastRatio 用的 WCAG 相对亮度是不同维度——这里用于判断"是否过暗/过浅"
 * 的观感门槛（与后端 brand_color.py._lightness 同算法），不用对比度计算。
 */
function perceivedLightness(hex: string): number {
  const r = Number.parseInt(hex.slice(1, 3), 16) / 255;
  const g = Number.parseInt(hex.slice(3, 5), 16) / 255;
  const b = Number.parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  return (max + min) / 2;
}

export interface ColorValidation {
  ok: boolean;
  reason?: string;
  /** 与白底的对比度，用于展示 */
  ratio?: number;
}

export function validatePrimaryColor(color: string): ColorValidation {
  if (!HEX_RE.test(color)) {
    return { ok: false, reason: "主色必须是 #rrggbb 格式" };
  }
  const normalized = color.toLowerCase();
  const ratio = contrastRatio(normalized, "#ffffff");
  if (ratio < MIN_CONTRAST_ON_WHITE) {
    return {
      ok: false,
      ratio,
      reason: `主色与白底对比度 ${ratio.toFixed(2)}:1，低于门槛 ${MIN_CONTRAST_ON_WHITE}:1，请使用更深的颜色`,
    };
  }
  const lightness = perceivedLightness(normalized);
  if (lightness < MIN_LIGHTNESS) {
    return {
      ok: false,
      ratio,
      reason: "主色过暗（接近纯黑），请使用更亮的品牌色",
    };
  }
  if (lightness > MAX_LIGHTNESS) {
    return { ok: false, ratio, reason: "主色过浅，请使用更深的品牌色" };
  }
  return { ok: true, ratio };
}
