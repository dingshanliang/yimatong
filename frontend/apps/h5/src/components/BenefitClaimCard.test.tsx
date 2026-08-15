import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { post, get: vi.fn() } }));

import { BenefitClaimCard } from "./BenefitClaimCard";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

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
      data: {
        status: "pending",
        benefit_id: "benefit-1",
        claim_id: "claim-1",
      },
    });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="scan-token"
          onClaimed={onClaimed}
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
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="fresh-scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    expect(button?.textContent).toBe("领取红包");
    await act(async () => root.unmount());
  });

  it("keeps an empty OAuth URL unclaimed and retryable", async () => {
    post
      .mockResolvedValueOnce({
        data: {
          status: "require_wechat_auth",
          benefit_id: "benefit-1",
          auth_url_path: "/wechat/auth-url",
        },
      })
      .mockResolvedValueOnce({ data: { auth_url: "" } });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="secret-scan-token"
          onClaimed={onClaimed}
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

    expect(post).toHaveBeenNthCalledWith(
      2,
      "/wechat/auth-url",
      {
        benefit_id: "benefit-1",
        scan_token: "secret-scan-token",
        consent_granted: true,
      },
      { signal: expect.any(AbortSignal) }
    );
    expect(post.mock.calls[1]?.[0]).not.toContain("secret-scan-token");
    expect(claimButton?.textContent).toBe("领取红包");
    expect(claimButton?.disabled).toBe(false);
    expect(container.textContent).toContain("授权地址");
    expect(onClaimed).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it.each([
    "javascript:alert(1)",
    "http://open.weixin.qq.com/connect/oauth2/authorize",
    "https://phishing.example/connect/oauth2/authorize",
  ])("rejects an unsafe OAuth URL %s without claiming", async (authUrl) => {
    post
      .mockResolvedValueOnce({
        data: {
          status: "require_wechat_auth",
          benefit_id: "benefit-1",
          auth_url_path: "/wechat/auth-url",
        },
      })
      .mockResolvedValueOnce({ data: { auth_url: authUrl } });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    await act(async () =>
      container
        .querySelector<HTMLInputElement>('input[type="checkbox"]')
        ?.click()
    );
    const button = container.querySelector<HTMLButtonElement>("button");
    await act(async () => button?.click());

    expect(button?.textContent).toBe("领取红包");
    expect(button?.disabled).toBe(false);
    expect(container.textContent).toContain("授权地址");
    expect(onClaimed).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("redirects only to the documented HTTPS WeChat origin without marking claimed first", async () => {
    const authUrl =
      "https://open.weixin.qq.com/connect/oauth2/authorize?appid=test#wechat_redirect";
    post
      .mockResolvedValueOnce({
        data: {
          status: "require_wechat_auth",
          benefit_id: "benefit-1",
          auth_url_path: "/wechat/auth-url",
        },
      })
      .mockResolvedValueOnce({ data: { auth_url: authUrl } });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="cash_red_packet"
          title="现金红包"
          scanToken="scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    await act(async () =>
      container
        .querySelector<HTMLInputElement>('input[type="checkbox"]')
        ?.click()
    );
    const originalWindow = globalThis.window;
    const redirectedLocation = { href: "" };
    vi.stubGlobal("window", { location: redirectedLocation });
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );

    expect(redirectedLocation.href).toBe(authUrl);
    expect(onClaimed).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("已领取");
    vi.stubGlobal("window", originalWindow);
    await act(async () => root.unmount());
  });

  it.each([
    { status: "delivered", benefit_id: "benefit-1", amount: 500 },
    { status: "claimed", benefit_id: "benefit-1" },
    { status: "pending", benefit_id: "another-benefit", claim_id: "claim-1" },
  ])(
    "rejects an unknown or malformed successful response %# without claiming",
    async (data) => {
      post.mockResolvedValue({ data });
      const onClaimed = vi.fn();
      const root = createRoot(container);
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId="benefit-1"
            benefitType="platform_coupon"
            title="优惠券"
            scanToken="scan-token"
            onClaimed={onClaimed}
          />
        );
      });
      const button = container.querySelector<HTMLButtonElement>("button");
      await act(async () => button?.click());

      expect(button?.textContent).toBe("立即领取");
      expect(button?.disabled).toBe(false);
      expect(container.textContent).toContain("领取结果异常");
      expect(onClaimed).not.toHaveBeenCalled();
      await act(async () => root.unmount());
    }
  );

  it("accepts only a well-formed claimed response as direct success", async () => {
    post.mockResolvedValue({
      data: {
        status: "claimed",
        benefit_id: "benefit-1",
        claim_id: "claim-1",
      },
    });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="platform_coupon"
          title="优惠券"
          scanToken="scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    const button = container.querySelector<HTMLButtonElement>("button");
    await act(async () => button?.click());

    expect(button?.textContent).toBe("已领取");
    expect(button?.disabled).toBe(true);
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="platform_coupon"
          title="优惠券"
          scanToken="fresh-scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    expect(button?.textContent).toBe("立即领取");
    expect(button?.disabled).toBe(false);
    await act(async () => root.unmount());
  });

  it("clears stale guidance and can retry immediately when the scan token changes", async () => {
    post
      .mockRejectedValueOnce({
        response: {
          status: 403,
          data: {
            detail: {
              code: "require_wecom_contact",
              message: "请先添加企业微信",
              qr_code: "https://cdn.example/wecom.png",
            },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          status: "claimed",
          benefit_id: "benefit-1",
          claim_id: "claim-2",
        },
      });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    const render = async (scanToken: string) => {
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId="benefit-1"
            benefitType="platform_coupon"
            title="优惠券"
            scanToken={scanToken}
            onClaimed={onClaimed}
          />
        );
      });
    };
    await render("stale-token");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    expect(container.textContent).toContain("请先添加企业微信");

    await render("fresh-token");
    expect(container.textContent).not.toContain("请先添加企业微信");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );

    expect(post).toHaveBeenCalledTimes(2);
    expect(container.querySelector("button")?.textContent).toBe("已领取");
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it.each([
    ["launch_release_not_current", "请重新扫码"],
    ["benefit_not_in_launch_release", "请刷新页面"],
    ["unexpected_conflict", "领取失败"],
  ])(
    "keeps the benefit retryable and does not report claimed for 409 code %s",
    async (code, expectedMessage) => {
      post.mockRejectedValue({
        response: {
          status: 409,
          data: { detail: { code, message: "backend detail" } },
        },
      });
      const onClaimed = vi.fn();
      const root = createRoot(container);
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId="benefit-1"
            benefitType="platform_coupon"
            title="优惠券"
            scanToken="stale-scan-token"
            onClaimed={onClaimed}
          />
        );
      });
      const button = container.querySelector<HTMLButtonElement>("button");
      await act(async () => button?.click());

      expect(button?.textContent).toBe("立即领取");
      expect(button?.disabled).toBe(false);
      expect(container.textContent).toContain(expectedMessage);
      expect(onClaimed).not.toHaveBeenCalled();
      await act(async () => root.unmount());
    }
  );

  it("accepts only an explicit idempotent replay conflict as already claimed", async () => {
    post.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { code: "replayed", message: "already claimed" } },
      },
    });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <BenefitClaimCard
          benefitId="benefit-1"
          benefitType="platform_coupon"
          title="优惠券"
          scanToken="scan-token"
          onClaimed={onClaimed}
        />
      );
    });
    const button = container.querySelector<HTMLButtonElement>("button");
    await act(async () => button?.click());

    expect(button?.textContent).toBe("已领取");
    expect(button?.disabled).toBe(true);
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it("can retry immediately when a new scan supplies a fresh token", async () => {
    post
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: {
            detail: { code: "launch_release_not_current", message: "stale" },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          status: "claimed",
          benefit_id: "benefit-1",
          claim_id: "claim-3",
        },
      });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    const render = async (scanToken: string) => {
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId="benefit-1"
            benefitType="platform_coupon"
            title="优惠券"
            scanToken={scanToken}
            onClaimed={onClaimed}
          />
        );
      });
    };
    await render("stale-token");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    await render("fresh-token");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );

    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenLastCalledWith(
      "/benefit-claims",
      {
        benefit_id: "benefit-1",
      },
      {
        signal: expect.any(AbortSignal),
        headers: { Authorization: "Bearer fresh-token" },
      }
    );
    expect(container.querySelector("button")?.textContent).toBe("已领取");
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it.each(["claimed", "pending", "error"])(
    "ignores a late %s completion from the previous scan while the fresh request is active",
    async (oldOutcome) => {
      const oldRequest = deferred<{ data: Record<string, unknown> }>();
      const freshRequest = deferred<{ data: Record<string, unknown> }>();
      post
        .mockReturnValueOnce(oldRequest.promise)
        .mockReturnValueOnce(freshRequest.promise);
      const onClaimed = vi.fn();
      const root = createRoot(container);
      const render = async (scanToken: string) => {
        await act(async () => {
          root.render(
            <BenefitClaimCard
              benefitId="benefit-1"
              benefitType="platform_coupon"
              title="优惠券"
              scanToken={scanToken}
              onClaimed={onClaimed}
            />
          );
        });
      };

      await render("old-token");
      await act(async () =>
        container.querySelector<HTMLButtonElement>("button")?.click()
      );
      const oldSignal = post.mock.calls[0]?.[2]?.signal as AbortSignal;
      await render("fresh-token");
      expect(oldSignal.aborted).toBe(true);
      await act(async () =>
        container.querySelector<HTMLButtonElement>("button")?.click()
      );
      expect(post).toHaveBeenCalledTimes(2);

      await act(async () => {
        if (oldOutcome === "error") {
          oldRequest.reject(new Error("late failure"));
        } else {
          oldRequest.resolve({
            data: {
              status: oldOutcome,
              benefit_id: "benefit-1",
              claim_id: "old-claim",
            },
          });
        }
        await Promise.resolve();
      });
      expect(container.querySelector("button")?.textContent).toBe("领取中...");
      expect(container.textContent).not.toContain("领取失败");
      expect(onClaimed).not.toHaveBeenCalled();

      await act(async () => {
        freshRequest.resolve({
          data: {
            status: "claimed",
            benefit_id: "benefit-1",
            claim_id: "fresh-claim",
          },
        });
        await Promise.resolve();
      });
      expect(container.querySelector("button")?.textContent).toBe("已领取");
      expect(onClaimed).toHaveBeenCalledTimes(1);
      await act(async () => root.unmount());
    }
  );

  it("ignores a late phone-claim completion after a fresh token succeeds", async () => {
    const oldPhoneRequest = deferred<{ data: Record<string, unknown> }>();
    post
      .mockRejectedValueOnce({
        response: {
          status: 403,
          data: { detail: { code: "require_auth", message: "phone" } },
        },
      })
      .mockReturnValueOnce(oldPhoneRequest.promise)
      .mockResolvedValueOnce({
        data: {
          status: "claimed",
          benefit_id: "benefit-1",
          claim_id: "fresh-claim",
        },
      });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    const render = async (scanToken: string) => {
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId="benefit-1"
            benefitType="platform_coupon"
            title="优惠券"
            scanToken={scanToken}
            onClaimed={onClaimed}
          />
        );
      });
    };

    await render("old-token");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    const phoneInput =
      container.querySelector<HTMLInputElement>('input[type="tel"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value"
      )?.set;
      setter?.call(phoneInput, "13800138000");
      phoneInput?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const confirmButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "确认授权"
    );
    await act(async () => confirmButton?.click());
    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/benefit-claims",
      { benefit_id: "benefit-1", phone: "13800138000" },
      {
        headers: { Authorization: "Bearer old-token" },
        signal: expect.any(AbortSignal),
      }
    );

    await render("fresh-token");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    expect(post).toHaveBeenCalledTimes(3);
    expect(container.querySelector("button")?.textContent).toBe("已领取");
    expect(onClaimed).toHaveBeenCalledTimes(1);

    await act(async () => {
      oldPhoneRequest.resolve({
        data: {
          status: "pending",
          benefit_id: "benefit-1",
          claim_id: "old-phone-claim",
        },
      });
      await Promise.resolve();
    });
    expect(container.querySelector("button")?.textContent).toBe("已领取");
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it.each(["success", "error"])(
    "ignores a stale auth-url %s completion after the token changes",
    async (authOutcome) => {
      const oldAuthRequest = deferred<{ data: { auth_url: string } }>();
      post
        .mockResolvedValueOnce({
          data: {
            status: "require_wechat_auth",
            benefit_id: "benefit-1",
            auth_url_path: "/wechat/auth-url",
          },
        })
        .mockReturnValueOnce(oldAuthRequest.promise)
        .mockResolvedValueOnce({
          data: {
            status: "claimed",
            benefit_id: "benefit-1",
            claim_id: "fresh-claim",
          },
        });
      const onClaimed = vi.fn();
      const root = createRoot(container);
      const render = async (scanToken: string) => {
        await act(async () => {
          root.render(
            <BenefitClaimCard
              benefitId="benefit-1"
              benefitType="cash_red_packet"
              title="现金红包"
              scanToken={scanToken}
              onClaimed={onClaimed}
            />
          );
        });
      };

      await render("old-token");
      await act(async () =>
        container
          .querySelector<HTMLInputElement>('input[type="checkbox"]')
          ?.click()
      );
      await act(async () =>
        container.querySelector<HTMLButtonElement>("button")?.click()
      );
      expect(post).toHaveBeenCalledTimes(2);
      const originalWindow = globalThis.window;
      const redirectedLocation = { href: "" };
      vi.stubGlobal("window", { location: redirectedLocation });

      await render("fresh-token");
      await act(async () => {
        if (authOutcome === "error") {
          oldAuthRequest.reject(new Error("late auth failure"));
        } else {
          oldAuthRequest.resolve({
            data: {
              auth_url:
                "https://open.weixin.qq.com/connect/oauth2/authorize?appid=old#wechat_redirect",
            },
          });
        }
        await Promise.resolve();
      });
      expect(redirectedLocation.href).toBe("");
      expect(onClaimed).not.toHaveBeenCalled();

      await act(async () =>
        container
          .querySelector<HTMLInputElement>('input[type="checkbox"]')
          ?.click()
      );
      await act(async () =>
        container.querySelector<HTMLButtonElement>("button")?.click()
      );
      expect(post).toHaveBeenCalledTimes(3);
      expect(onClaimed).toHaveBeenCalledTimes(1);
      vi.stubGlobal("window", originalWindow);
      await act(async () => root.unmount());
    }
  );

  it("invalidates an unresolved request when the benefit identity changes", async () => {
    const oldRequest = deferred<{ data: Record<string, unknown> }>();
    post.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce({
      data: {
        status: "claimed",
        benefit_id: "benefit-2",
        claim_id: "fresh-benefit-claim",
      },
    });
    const onClaimed = vi.fn();
    const root = createRoot(container);
    const render = async (benefitId: string) => {
      await act(async () => {
        root.render(
          <BenefitClaimCard
            benefitId={benefitId}
            benefitType="platform_coupon"
            title="优惠券"
            scanToken="same-token"
            onClaimed={onClaimed}
          />
        );
      });
    };

    await render("benefit-1");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    await render("benefit-2");
    await act(async () =>
      container.querySelector<HTMLButtonElement>("button")?.click()
    );
    expect(post).toHaveBeenCalledTimes(2);

    await act(async () => {
      oldRequest.resolve({
        data: {
          status: "claimed",
          benefit_id: "benefit-1",
          claim_id: "old-benefit-claim",
        },
      });
      await Promise.resolve();
    });
    expect(container.querySelector("button")?.textContent).toBe("已领取");
    expect(onClaimed).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });
});
