import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { post, get: vi.fn() } }));

import { BenefitClaimCard } from "./BenefitClaimCard";

describe("BenefitClaimCard delivery state", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    post.mockReset();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("shows an acknowledged connector claim as pending instead of delivered", async () => {
    post.mockResolvedValue({
      data: { status: "pending", claim_id: "claim-1" },
    });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="scan-token"
        />
      );
    });
    await act(async () => {
      const checkbox = container.querySelector<HTMLInputElement>(
        'input[type="checkbox"]'
      );
      checkbox?.click();
    });
    const button = container.querySelector("button");
    await act(async () => button?.click());

    expect(button?.textContent).toBe("发放处理中");
    expect(button?.hasAttribute("disabled")).toBe(true);
    expect(container.textContent).not.toContain("已领取");
    await act(async () => root.unmount());
  });

  it("starts OAuth with a POST body so the scan credential never enters the request URL", async () => {
    post
      .mockResolvedValueOnce({
        data: {
          status: "require_wechat_auth",
          auth_url_path: "/wechat/auth-url",
        },
      })
      .mockResolvedValueOnce({ data: { auth_url: "" } });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="secret-scan-token"
        />
      );
    });
    const claimButton = container.querySelector<HTMLButtonElement>("button");
    expect(claimButton?.disabled).toBe(true);
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[type="checkbox"]')
        ?.click();
    });
    expect(claimButton?.disabled).toBe(false);
    await act(async () => claimButton?.click());

    expect(post).toHaveBeenNthCalledWith(2, "/wechat/auth-url", {
      benefit_id: "benefit-1",
      scan_token: "secret-scan-token",
      consent_granted: true,
    });
    expect(post.mock.calls[1]?.[0]).not.toContain("secret-scan-token");
    await act(async () => root.unmount());
  });
});
