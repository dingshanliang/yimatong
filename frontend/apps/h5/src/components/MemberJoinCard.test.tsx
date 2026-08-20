import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { get, post } }));

import { MemberJoinCard } from "./MemberJoinCard";

describe("MemberJoinCard", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    get.mockReset();
    post.mockReset();
    localStorage.clear();
    window.history.replaceState({}, "", "/c/PUBLIC-MEMBER");
    get.mockResolvedValue({
      data: {
        purpose: "brand_membership",
        policy_version: "member-v1",
        policy_digest: "a".repeat(64),
        policy_title: "品牌会员规则",
        policy_content: "自愿加入，可单独选择是否接收营销消息。",
      },
    });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("does not create membership until explicit agreement", async () => {
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberJoinCard scanToken="anonymous-scan-token" />);
    });
    await act(async () => Promise.resolve());

    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
    expect(container.textContent).toContain("匿名查看产品与溯源不受影响");
    await act(async () => root.unmount());
  });

  it("persists consent before membership and rotates the scan credential", async () => {
    post
      .mockResolvedValueOnce({
        data: { consent_id: "consent-1", status: "granted" },
      })
      .mockResolvedValueOnce({
        data: {
          membership_id: "membership-1",
          membership_number: "MBR-202608200001",
          consumer_id: "consumer-1",
          scan_token: "member-bound-token",
        },
      });
    const onScanTokenChange = vi.fn();
    const onMembershipReady = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <MemberJoinCard
          scanToken="anonymous-scan-token"
          onScanTokenChange={onScanTokenChange}
          onMembershipReady={onMembershipReady}
        />
      );
    });
    await act(async () => Promise.resolve());
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[type="checkbox"]')
        ?.click();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    expect(post.mock.calls[0][0]).toBe("/public/consents");
    expect(post.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        purpose: "brand_membership",
        idempotency_key: expect.any(String),
      })
    );
    expect(post.mock.calls[1][0]).toBe("/consumers/membership/join");
    expect(post.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        consent_id: "consent-1",
        idempotency_key: expect.any(String),
      })
    );
    expect(localStorage.getItem("scan_token")).toBe("member-bound-token");
    expect(onScanTokenChange).toHaveBeenCalledWith("member-bound-token");
    expect(onMembershipReady).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("MBR-202608200001");
    await act(async () => root.unmount());
  });

  it("consumes a recovery token from the URL fragment without leaking it into navigation", async () => {
    window.history.replaceState(
      {},
      "",
      "/c/PUBLIC-MEMBER#member_recovery_token=signed-recovery-token"
    );
    post.mockResolvedValueOnce({
      data: {
        membership_id: "membership-1",
        membership_number: "MBR-RECOVER00001",
        consumer_id: "consumer-2",
        status: "active",
      },
    });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberJoinCard scanToken="consumer-bound-scan-token" />);
    });
    await act(async () => Promise.resolve());

    expect(window.location.hash).toBe("");
    expect(container.textContent).toContain("恢复品牌会员身份");
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    expect(post).toHaveBeenCalledWith(
      "/consumers/membership/recover",
      {
        recovery_token: "signed-recovery-token",
        idempotency_key: expect.any(String),
      },
      { headers: { Authorization: "Bearer consumer-bound-scan-token" } }
    );
    expect(container.textContent).toContain("已恢复品牌会员");
    expect(container.textContent).toContain("MBR-RECOVER00001");
    await act(async () => root.unmount());
  });
});
