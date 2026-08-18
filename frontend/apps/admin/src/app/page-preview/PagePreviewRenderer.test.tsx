import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PagePreviewRenderer } from "./PagePreviewRenderer";

function postDsl(dsl: Record<string, unknown>) {
  window.dispatchEvent(
    new MessageEvent("message", {
      data: {
        type: "preview-dsl",
        payload: { dsl, previewContext: {}, previewMode: "example" },
      },
    })
  );
}

describe("PagePreviewRenderer", () => {
  it("应用租户品牌圆角/背景槽位并渲染 benefit_card 模块", async () => {
    render(<PagePreviewRenderer />);
    postDsl({
      modules: [
        { id: "hero", type: "product_hero", enabled: true },
        { id: "benefit", type: "benefit_card", enabled: true },
      ],
      tenant_branding: {
        name: "品牌预览",
        primary_color: "#15803d",
        radius_preset: "lg",
        background_preset: "tinted",
      },
    });
    const main = await waitFor(() => {
      const el = screen.getByRole("main");
      expect(el.textContent).toContain("领取权益");
      return el;
    });
    expect(main.style.getPropertyValue("--ymt-radius-lg")).toBe("18px");
    // tinted = mixWithWhite(#15803d, 0.95)，与 H5 brand-theme.ts 同算法
    // （jsdom 将 background 归一化为 rgb() 形式）
    expect(main.style.background).toBe("rgb(243, 249, 245)");
    expect(main.textContent).not.toContain("未知模块");
  });

  it("无租户品牌时回落 md 圆角与 canvas 背景（与全局 token 观感一致）", async () => {
    render(<PagePreviewRenderer />);
    postDsl({
      modules: [{ id: "hero", type: "product_hero", enabled: true }],
    });
    const main = await waitFor(() => {
      const el = screen.getByRole("main");
      expect(el.textContent).toContain("产品名称预览");
      return el;
    });
    expect(main.style.getPropertyValue("--ymt-radius-lg")).toBe("14px");
    expect(main.style.background).toBe("rgb(246, 248, 245)");
  });
});
