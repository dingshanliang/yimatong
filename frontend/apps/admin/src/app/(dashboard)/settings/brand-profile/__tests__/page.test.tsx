import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BrandProfilePage from "../page";

const mockMessage = { success: vi.fn(), error: vi.fn() };
const mockGet = vi.fn();
const mockPatch = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  // ColorPicker 用桩替换：onClick 同时给 antd 6 真实签名
  // (color, toCssString=rgb()串)；页面必须取 color.toHexString()。
  const FakeColorPicker = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (c: { toHexString: () => string }, hex: string) => void;
  }) => (
    <button
      type="button"
      aria-label="触发取色"
      onClick={() =>
        onChange?.({ toHexString: () => "#B91C1C" }, "rgb(185, 28, 28)")
      }
    >
      {value}
    </button>
  );
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mockMessage }),
    },
    ColorPicker: FakeColorPicker,
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
  extractErrorMessage: () => "操作失败",
}));

// ImageUploadInput 简化为受控 input，避免触碰上传链路
vi.mock("@/components/ImageUploadInput", () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v?: string) => void;
  }) => (
    <input
      aria-label="品牌 Logo"
      value={value || ""}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

// design-tokens 未在 vitest alias 配置，用共享 mock（vi.hoisted 解决工厂提升后的引用顺序）
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

describe("BrandProfilePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({
      data: {
        id: "t1",
        brand_profile: {
          primary_color: "#1F7A4D",
          radius_preset: "md",
          background_preset: "tinted",
          hide_yimatong_brand: true,
          logo_url: "https://cdn.example.com/logo.png",
          support_phone: "400-123-4567",
          support_wecom_url: "https://work.weixin.qq.com/kabc/example",
        },
      },
    });
    mockPatch.mockResolvedValue({ data: {} });
  });

  it("加载并回显已保存的 logo_url", async () => {
    render(<BrandProfilePage />);
    const logoInput = await screen.findByLabelText("品牌 Logo");
    expect(logoInput).toHaveValue("https://cdn.example.com/logo.png");
  });

  it("点击保存时调用 PATCH /tenants/me 传入 brand_profile 七槽位（含客服槽位）", async () => {
    render(<BrandProfilePage />);
    await screen.findByLabelText("品牌 Logo");
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/tenants/me", {
        brand_profile: expect.objectContaining({
          primary_color: "#1f7a4d",
          radius_preset: "md",
          background_preset: "tinted",
          hide_yimatong_brand: true,
          logo_url: "https://cdn.example.com/logo.png",
          support_phone: "400-123-4567",
          support_wecom_url: "https://work.weixin.qq.com/kabc/example",
        }),
      });
    });
    expect(mockMessage.success).toHaveBeenCalledWith("品牌配置已保存");
  });

  it("加载空 brand_profile 时不崩溃，保存按钮可用（默认主色合规）", async () => {
    mockGet.mockResolvedValue({ data: { id: "t1", brand_profile: null } });
    render(<BrandProfilePage />);
    await screen.findByLabelText("品牌 Logo");
    expect(screen.getByRole("button", { name: "保存配置" })).not.toBeDisabled();
  });

  it("保存成功后刷新本地 profile", async () => {
    render(<BrandProfilePage />);
    await screen.findByLabelText("品牌 Logo");
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mockMessage.success).toHaveBeenCalled());
  });

  it("取色器改色后保存使用 toHexString 的 #rrggbb 值（antd 6 rgb() 第二参回归）", async () => {
    render(<BrandProfilePage />);
    await screen.findByLabelText("品牌 Logo");
    fireEvent.click(screen.getByRole("button", { name: "触发取色" }));
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/tenants/me", {
        brand_profile: expect.objectContaining({ primary_color: "#b91c1c" }),
      });
    });
    expect(mockMessage.error).not.toHaveBeenCalled();
  });
});
