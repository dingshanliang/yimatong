import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { post } }));

import { ShopRedirect } from "./ShopRedirect";

const memberShop = {
  platform: "other" as const,
  name: "品牌轻商城",
  url: "https://shop.example/member#source=package",
  commerce_connection_id: "6e1465bd-680b-4432-baf4-66bef68398bd",
};

describe("ShopRedirect member handoff", () => {
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

  it("opens the configured shop with an opaque handoff in the URL fragment", async () => {
    const replace = vi.fn();
    const pendingWindow = {
      opener: window,
      location: { replace },
      close: vi.fn(),
    };
    const open = vi
      .spyOn(window, "open")
      .mockReturnValue(pendingWindow as unknown as Window);
    post.mockResolvedValue({
      data: { handoff_token: "signed-member-handoff-token" },
    });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <ShopRedirect shops={[memberShop]} scanToken="member-scan-token" />
      );
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(pendingWindow.opener).toBeNull();
    expect(post).toHaveBeenCalledWith(
      "/consumers/membership/commerce-handoffs",
      {
        connection_id: memberShop.commerce_connection_id,
        idempotency_key: expect.any(String),
      },
      { headers: { Authorization: "Bearer member-scan-token" } }
    );
    expect(replace).toHaveBeenCalledWith(
      "https://shop.example/member#source=package&yimatong_handoff=signed-member-handoff-token"
    );
    await act(async () => root.unmount());
  });

  it("keeps an explicit anonymous fallback when member handoff fails", async () => {
    const pendingWindow = {
      opener: window,
      location: { replace: vi.fn() },
      close: vi.fn(),
    };
    vi.spyOn(window, "open").mockReturnValue(
      pendingWindow as unknown as Window
    );
    post.mockRejectedValue(new Error("handoff unavailable"));
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <ShopRedirect shops={[memberShop]} scanToken="member-scan-token" />
      );
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    const fallback = container.querySelector<HTMLAnchorElement>("a");
    expect(pendingWindow.close).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("会员身份暂时无法带入商城");
    expect(fallback?.href).toBe(memberShop.url);
    expect(fallback?.textContent).toContain("匿名进入品牌轻商城");
    await act(async () => root.unmount());
  });

  it("does not request identity handoff for an anonymous visitor", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const root = createRoot(container);
    await act(async () => {
      root.render(<ShopRedirect shops={[memberShop]} />);
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    expect(post).not.toHaveBeenCalled();
    expect(open).toHaveBeenCalledWith(
      memberShop.url,
      "_blank",
      "noopener,noreferrer"
    );
    await act(async () => root.unmount());
  });
});
