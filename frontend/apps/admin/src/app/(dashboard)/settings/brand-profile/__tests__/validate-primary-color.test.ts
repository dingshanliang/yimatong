import { beforeEach, describe, expect, it, vi } from "vitest";

// design-tokens 未在 vitest alias 中配置，用 vi.hoisted 内联真实 WCAG 算法。
// validatePrimaryColor 的契约是：与后端 brand_color.py 同门槛（对比度≥3:1 + 亮度安全域）。
const { mock } = vi.hoisted(() => {
  function channelToLinear(channel: number): number {
    const n = channel / 255;
    return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  }
  function luminance(hex: string): number {
    const [r, g, b] = [1, 3, 5].map((i) =>
      channelToLinear(Number.parseInt(hex.slice(i, i + 2), 16))
    );
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  return {
    mock: {
      contrastRatio: (fg: string, bg: string) => {
        const a = luminance(fg.toLowerCase());
        const b = luminance(bg.toLowerCase());
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      },
    },
  };
});
vi.mock("@yimatong/design-tokens", () => mock);

// mock 后再 import 被测模块
const { validatePrimaryColor } = await import("../_lib/validate-primary-color");

describe("validatePrimaryColor（前端主色预校验）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("接受安全的品牌绿", () => {
    const r = validatePrimaryColor("#16a34a");
    expect(r.ok).toBe(true);
    expect(r.ratio).toBeGreaterThan(3);
  });

  it.each(["#16a34a", "#1F7A4D", "#7c3aed", "#b45309", "#0f766e"])(
    "接受安全色 %s",
    (color) => {
      expect(validatePrimaryColor(color).ok).toBe(true);
    }
  );

  it("拒绝纯白（对比度 1:1）", () => {
    const r = validatePrimaryColor("#ffffff");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/对比度/);
  });

  it("拒绝亮黄（对白底对比度不足 3:1）", () => {
    expect(validatePrimaryColor("#fde047").ok).toBe(false);
  });

  it("拒绝近黑（亮度过低）", () => {
    const r = validatePrimaryColor("#000000");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/暗|黑/);
  });

  it("拒绝过浅色", () => {
    expect(validatePrimaryColor("#a3e635").ok).toBe(false);
  });

  it("拒绝非法格式", () => {
    expect(validatePrimaryColor("red").ok).toBe(false);
    expect(validatePrimaryColor("#abc").ok).toBe(false);
    expect(validatePrimaryColor("#gggggg").ok).toBe(false);
    expect(validatePrimaryColor("").ok).toBe(false);
  });

  it("返回对比度数值用于展示", () => {
    const r = validatePrimaryColor("#15803d");
    expect(r.ok).toBe(true);
    expect(typeof r.ratio).toBe("number");
  });
});
