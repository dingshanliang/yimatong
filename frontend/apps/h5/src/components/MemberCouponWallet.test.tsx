import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { get, post } }));

import { MemberCouponWallet } from "./MemberCouponWallet";

const coupon = {
  id: "d3f42bdb-0362-47b3-830f-9449d062d26f",
  coupon_number: "RCP-0123456789ABCDEF",
  name: "复购立减 10 元",
  amount_minor: 1000,
  minimum_spend_minor: 5000,
  channel_scope: "both",
  status: "available",
  valid_from: "2026-08-20T00:00:00Z",
  valid_until: "2026-09-20T00:00:00Z",
};

describe("MemberCouponWallet", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    get.mockReset();
    post.mockReset();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("shows authoritative wallet state and commercial terms", async () => {
    get.mockResolvedValue({ data: [coupon] });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberCouponWallet scanToken="member-scan-token" />);
    });
    await act(async () => Promise.resolve());

    expect(get).toHaveBeenCalledWith("/consumers/membership/coupons", {
      headers: { Authorization: "Bearer member-scan-token" },
    });
    expect(container.textContent).toContain("复购立减 10 元");
    expect(container.textContent).toContain("商品金额满 ¥50 可用");
    expect(container.textContent).toContain("可使用");
    expect(container.textContent).not.toContain("积分");
    await act(async () => root.unmount());
  });

  it("creates a five-minute store redemption QR from the member credential", async () => {
    get.mockResolvedValue({ data: [coupon] });
    post.mockResolvedValue({
      data: {
        redemption_token: "signed-store-redemption-token",
        expires_in: 300,
      },
    });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberCouponWallet scanToken="member-scan-token" />);
    });
    await act(async () => Promise.resolve());
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("到店出示核销码"))
        ?.click();
    });

    expect(post).toHaveBeenCalledWith(
      `/consumers/membership/coupons/${coupon.id}/store-token`,
      {},
      { headers: { Authorization: "Bearer member-scan-token" } }
    );
    expect(
      container.querySelector('[aria-label="门店核销二维码"]')
    ).not.toBeNull();
    expect(container.textContent).toContain("核销码 5 分钟内有效");
    await act(async () => root.unmount());
  });

  it("stays hidden when the scan credential has no active membership", async () => {
    get.mockRejectedValue({ response: { status: 401 } });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberCouponWallet scanToken="anonymous-scan-token" />);
    });
    await act(async () => Promise.resolve());

    expect(container.textContent).toBe("");
    await act(async () => root.unmount());
  });
});
