import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post, remove } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ apiClient: { get, post, delete: remove } }));

import { MemberNotificationCenter } from "./MemberNotificationCenter";

const preference = { marketing_enabled: false, service_wechat_enabled: true };
const policy = {
  purpose: "lead_capture",
  policy_version: "2026-08",
  policy_digest: "policy-digest",
  policy_title: "品牌营销规则",
  policy_content: "用于优惠到期与复购提醒",
};

describe("MemberNotificationCenter", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    get.mockReset();
    post.mockReset();
    remove.mockReset();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    get.mockImplementation((path: string) => {
      if (path.endsWith("/notifications")) {
        return Promise.resolve({
          data: [
            {
              id: "notification-1",
              notification_class: "service",
              notification_type: "order_paid",
              title: "订单已支付",
              body: "订单事实已记录",
              occurred_at: "2026-08-20T03:00:00Z",
            },
          ],
        });
      }
      if (path.endsWith("/notification-preferences"))
        return Promise.resolve({ data: preference });
      return Promise.resolve({ data: policy });
    });
  });

  afterEach(() => {
    delete window.wx;
    vi.unstubAllGlobals();
    container.remove();
  });

  it("keeps service facts visible without WeChat authorization", async () => {
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberNotificationCenter scanToken="member-scan-token" />);
    });
    await act(async () => Promise.resolve());

    expect(container.textContent).toContain("订单已支付");
    expect(container.textContent).toContain("订单和优惠券事实会一直保留在这里");
    expect(container.textContent).toContain(
      "当前入口暂不支持微信订阅；消息中心不受影响"
    );
    expect(container.textContent).not.toContain("积分");
    await act(async () => root.unmount());
  });

  it("stays hidden for a scan credential that is not member-bound", async () => {
    get.mockRejectedValue({ response: { status: 401 } });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <MemberNotificationCenter scanToken="anonymous-scan-token" />
      );
    });
    await act(async () => Promise.resolve());

    expect(container.textContent).toBe("");
    await act(async () => root.unmount());
  });

  it("records channel authorization and marketing consent from one user action", async () => {
    vi.stubEnv(
      "NEXT_PUBLIC_WECHAT_COUPON_EXPIRY_TEMPLATE_ID",
      "template-coupon-expiry"
    );
    window.wx = {
      requestSubscribeMessage: ({ tmplIds, success }) =>
        success({ [tmplIds[0]]: "accept" }),
    };
    post
      .mockResolvedValueOnce({
        data: { consent_id: "consent-1", status: "granted" },
      })
      .mockResolvedValueOnce({
        data: { ...preference, marketing_enabled: true },
      });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberNotificationCenter scanToken="member-scan-token" />);
    });
    await act(async () => Promise.resolve());
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("开启优惠与复购提醒"))
        ?.click();
    });

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/public/consents",
      expect.objectContaining({ purpose: "lead_capture" }),
      { headers: { Authorization: "Bearer member-scan-token" } }
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/consumers/membership/notification-preferences/marketing-subscription",
      expect.objectContaining({
        template_code: "coupon_expiry",
        marketing_consent_id: "consent-1",
      }),
      { headers: { Authorization: "Bearer member-scan-token" } }
    );
    expect(container.textContent).toContain("优惠与复购微信提醒已开启");
    await act(async () => root.unmount());
  });

  it("immediately offers and persists marketing unsubscribe", async () => {
    get.mockImplementation((path: string) => {
      if (path.endsWith("/notifications")) return Promise.resolve({ data: [] });
      if (path.endsWith("/notification-preferences")) {
        return Promise.resolve({
          data: { ...preference, marketing_enabled: true },
        });
      }
      return Promise.resolve({ data: policy });
    });
    remove.mockResolvedValue({ data: preference });
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemberNotificationCenter scanToken="member-scan-token" />);
    });
    await act(async () => Promise.resolve());
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("退订营销提醒"))
        ?.click();
    });

    expect(remove).toHaveBeenCalledWith(
      "/consumers/membership/notification-preferences/marketing-subscription",
      { headers: { Authorization: "Bearer member-scan-token" } }
    );
    await act(async () => root.unmount());
  });
});
