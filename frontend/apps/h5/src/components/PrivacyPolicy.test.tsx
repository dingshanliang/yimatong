import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { get, post } }));

import { PrivacyPolicy } from "./PrivacyPolicy";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

describe("PrivacyPolicy durable consent state", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    post.mockReset();
    get.mockReset();
    get.mockResolvedValue({
      data: {
        purpose: "privacy_policy",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
        policy_title: "隐私政策",
        policy_content: "server policy",
      },
    });
    localStorage.clear();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    container.remove();
  });

  it("does not accept or notify before the durable receipt is returned", async () => {
    const request = deferred<{
      data: { consent_id: string; status: string };
    }>();
    post.mockReturnValue(request.promise);
    const onAccept = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
          onAccept={onAccept}
        />
      );
    });

    const accept = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("同意")
    );
    await act(async () => accept?.click());

    expect(container.textContent).not.toContain("已同意隐私政策");
    expect(onAccept).not.toHaveBeenCalled();

    await act(async () => {
      request.resolve({
        data: { consent_id: "receipt-1", status: "granted" },
      });
      await request.promise;
    });
    expect(container.textContent).toContain("已同意隐私政策");
    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("consent_id:code-1")).toBe("receipt-1");
    await act(async () => root.unmount());
  });

  it("restores accepted state only after the stored receipt is validated for the current scan subject", async () => {
    localStorage.setItem("consent_id:code-1", "receipt-1");
    get.mockResolvedValueOnce({
      data: {
        purpose: "privacy_policy",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
        policy_title: "隐私政策",
        policy_content: "server policy",
      },
    });
    get.mockResolvedValueOnce({
      data: {
        consent_id: "receipt-1",
        status: "granted",
        purpose: "privacy_policy",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
        current_policy: true,
      },
    });
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
        />
      );
    });

    expect(get).toHaveBeenCalledWith(
      "/public/consents/receipt-1/status",
      expect.objectContaining({
        headers: { Authorization: "Bearer scan-token" },
      })
    );
    expect(container.textContent).toContain("已同意隐私政策");
    expect(container.textContent).toContain("撤回授权");
    await act(async () => root.unmount());
  });

  it("retries a retryable receipt status without deleting the locator and restores on success", async () => {
    vi.useFakeTimers();
    localStorage.setItem("consent_id:code-1", "receipt-1");
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "privacy_policy",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
          policy_title: "隐私政策",
          policy_content: "server policy",
        },
      })
      .mockRejectedValueOnce({
        response: { status: 409, data: { detail: "consent_authority_retry" } },
      })
      .mockResolvedValueOnce({
        data: {
          consent_id: "receipt-1",
          status: "granted",
          purpose: "privacy_policy",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
        />
      );
    });

    expect(localStorage.getItem("consent_id:code-1")).toBe("receipt-1");
    expect(container.textContent).not.toContain("已同意隐私政策");
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(container.textContent).toContain("已同意隐私政策");
    expect(
      get.mock.calls.filter(
        ([url]) => url === "/public/consents/receipt-1/status"
      )
    ).toHaveLength(2);
    await act(async () => root.unmount());
  });

  it("cancels a scheduled receipt retry when the resolver token changes", async () => {
    vi.useFakeTimers();
    localStorage.setItem("consent_id:code-1", "old-receipt");
    get.mockImplementation(
      (url: string, config?: { headers?: { Authorization?: string } }) => {
        if (url === "/public/consents/policy") {
          return Promise.resolve({
            data: {
              purpose: "privacy_policy",
              policy_version: "v2",
              policy_digest: "a".repeat(64),
              policy_title: "隐私政策",
              policy_content: "server policy",
            },
          });
        }
        if (config?.headers?.Authorization === "Bearer old-token") {
          return Promise.reject({
            response: {
              status: 409,
              data: { detail: "consent_authority_retry" },
            },
          });
        }
        return Promise.resolve({
          data: {
            consent_id: "fresh-receipt",
            status: "granted",
            purpose: "privacy_policy",
            policy_version: "v2",
            policy_digest: "a".repeat(64),
          },
        });
      }
    );
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="old-token"
        />
      );
    });
    expect(localStorage.getItem("consent_id:code-1")).toBe("old-receipt");

    localStorage.setItem("consent_id:code-1", "fresh-receipt");
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="fresh-token"
        />
      );
    });
    expect(container.textContent).toContain("已同意隐私政策");

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(
      get.mock.calls.filter(
        ([url, config]) =>
          url === "/public/consents/old-receipt/status" &&
          config?.headers?.Authorization === "Bearer old-token"
      )
    ).toHaveLength(1);
    expect(localStorage.getItem("consent_id:code-1")).toBe("fresh-receipt");
    await act(async () => root.unmount());
  });

  it.each([
    [403, "consent_authority_denied"],
    [404, "consent_authority_not_found"],
    [409, "consent_authority_invalid"],
  ])(
    "clears the locator after authoritative status failure %s %s",
    async (status, detail) => {
      localStorage.setItem("consent_id:code-1", "receipt-1");
      get
        .mockResolvedValueOnce({
          data: {
            purpose: "privacy_policy",
            policy_version: "v2",
            policy_digest: "a".repeat(64),
            policy_title: "隐私政策",
            policy_content: "server policy",
          },
        })
        .mockRejectedValueOnce({ response: { status, data: { detail } } });
      const root = createRoot(container);

      await act(async () => {
        root.render(
          <PrivacyPolicy
            content="policy"
            publicId="code-1"
            scanToken="scan-token"
          />
        );
      });

      expect(localStorage.getItem("consent_id:code-1")).toBeNull();
      await act(async () => root.unmount());
    }
  );

  it("keeps the locator after an unknown status failure", async () => {
    localStorage.setItem("consent_id:code-1", "receipt-1");
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "privacy_policy",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
          policy_title: "隐私政策",
          policy_content: "server policy",
        },
      })
      .mockRejectedValueOnce({
        response: { status: 409, data: { detail: "unexpected_conflict" } },
      });
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
        />
      );
    });

    expect(localStorage.getItem("consent_id:code-1")).toBe("receipt-1");
    await act(async () => root.unmount());
  });

  it.each(["withdrawn", "granted"])(
    "clears a stored receipt when its status or policy is no longer current (%s)",
    async (status) => {
      localStorage.setItem("consent_id:code-1", "stale-receipt");
      get.mockResolvedValueOnce({
        data: {
          purpose: "privacy_policy",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
          policy_title: "隐私政策",
          policy_content: "server policy",
        },
      });
      get.mockResolvedValueOnce({
        data: {
          consent_id: "stale-receipt",
          status,
          purpose: "privacy_policy",
          policy_version: status === "granted" ? "v1" : "v2",
          policy_digest: "a".repeat(64),
        },
      });
      const root = createRoot(container);

      await act(async () => {
        root.render(
          <PrivacyPolicy
            content="policy"
            publicId="code-1"
            scanToken="scan-token"
          />
        );
      });

      expect(container.textContent).not.toContain("已同意隐私政策");
      expect(localStorage.getItem("consent_id:code-1")).toBeNull();
      await act(async () => root.unmount());
    }
  );

  it("ignores a late stored-receipt result after the resolver token changes", async () => {
    const oldStatus = deferred<{ data: Record<string, unknown> }>();
    localStorage.setItem("consent_id:code-1", "old-receipt");
    get.mockImplementation(
      (url: string, config?: { headers?: { Authorization?: string } }) => {
        if (url === "/public/consents/policy") {
          return Promise.resolve({
            data: {
              purpose: "privacy_policy",
              policy_version: "v2",
              policy_digest: "a".repeat(64),
              policy_title: "隐私政策",
              policy_content: "server policy",
            },
          });
        }
        if (config?.headers?.Authorization === "Bearer old-token") {
          return oldStatus.promise;
        }
        return Promise.resolve({
          data: {
            consent_id: "fresh-receipt",
            status: "granted",
            purpose: "privacy_policy",
            policy_version: "v2",
            policy_digest: "a".repeat(64),
          },
        });
      }
    );
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="old-token"
        />
      );
    });
    localStorage.setItem("consent_id:code-1", "fresh-receipt");
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="fresh-token"
        />
      );
    });
    expect(container.textContent).toContain("已同意隐私政策");

    await act(async () => {
      oldStatus.resolve({
        data: {
          consent_id: "old-receipt",
          status: "withdrawn",
        },
      });
      await oldStatus.promise;
    });

    expect(container.textContent).toContain("已同意隐私政策");
    expect(localStorage.getItem("consent_id:code-1")).toBe("fresh-receipt");
    await act(async () => root.unmount());
  });

  it("keeps the durable grant visible when withdrawal fails", async () => {
    post.mockResolvedValueOnce({
      data: { consent_id: "receipt-1", status: "granted" },
    });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
        />
      );
    });
    const accept = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("同意")
    );
    await act(async () => accept?.click());
    post.mockRejectedValueOnce(new Error("unavailable"));

    const withdraw = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "撤回授权"
    );
    await act(async () => withdraw?.click());
    const confirm = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "确认撤回"
    );
    await act(async () => confirm?.click());

    expect(container.textContent).toContain("已同意隐私政策");
    expect(localStorage.getItem("consent_id:code-1")).toBe("receipt-1");
    const failedKey = post.mock.calls[1][1].idempotency_key;
    post.mockResolvedValueOnce({ data: { status: "withdrawn" } });
    const retryWithdraw = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "撤回授权"
    );
    await act(async () => retryWithdraw?.click());
    const retryConfirm = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "确认撤回"
    );
    await act(async () => retryConfirm?.click());
    expect(post.mock.calls[2][1].idempotency_key).toBe(failedKey);
    expect(localStorage.getItem("consent_id:code-1")).toBeNull();
    await act(async () => root.unmount());
  });

  it("uses fresh idempotency keys after successful grant and withdrawal cycles", async () => {
    post
      .mockResolvedValueOnce({
        data: { consent_id: "receipt-1", status: "granted" },
      })
      .mockResolvedValueOnce({ data: { status: "withdrawn" } })
      .mockResolvedValueOnce({
        data: { consent_id: "receipt-2", status: "granted" },
      })
      .mockResolvedValueOnce({ data: { status: "withdrawn" } });
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="scan-token"
        />
      );
    });

    for (let cycle = 0; cycle < 2; cycle += 1) {
      const accept = Array.from(container.querySelectorAll("button")).find(
        (button) => button.textContent?.includes("同意")
      );
      await act(async () => accept?.click());
      const withdraw = Array.from(container.querySelectorAll("button")).find(
        (button) => button.textContent === "撤回授权"
      );
      await act(async () => withdraw?.click());
      const confirm = Array.from(container.querySelectorAll("button")).find(
        (button) => button.textContent === "确认撤回"
      );
      await act(async () => confirm?.click());
    }

    const firstGrantKey = post.mock.calls[0][1].idempotency_key;
    const firstWithdrawKey = post.mock.calls[1][1].idempotency_key;
    const secondGrantKey = post.mock.calls[2][1].idempotency_key;
    const secondWithdrawKey = post.mock.calls[3][1].idempotency_key;
    expect(secondGrantKey).not.toBe(firstGrantKey);
    expect(secondWithdrawKey).not.toBe(firstWithdrawKey);
    await act(async () => root.unmount());
  });

  it("does not fetch or grant without the explicit resolver scan token", async () => {
    const root = createRoot(container);
    await act(async () => {
      root.render(<PrivacyPolicy content="policy" publicId="code-1" />);
    });

    expect(get).not.toHaveBeenCalled();
    const accept = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("同意")
    );
    expect(accept?.hasAttribute("disabled")).toBe(true);
    await act(async () => accept?.click());
    expect(post).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("ignores a late grant receipt after the resolver scan token changes", async () => {
    const request = deferred<{
      data: { consent_id: string; status: string };
    }>();
    post.mockReturnValue(request.promise);
    const onAccept = vi.fn();
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="old-token"
          onAccept={onAccept}
        />
      );
    });
    const accept = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("同意")
    );
    await act(async () => accept?.click());
    await act(async () => {
      root.render(
        <PrivacyPolicy
          content="policy"
          publicId="code-1"
          scanToken="fresh-token"
          onAccept={onAccept}
        />
      );
    });
    await act(async () => {
      request.resolve({
        data: { consent_id: "stale-receipt", status: "granted" },
      });
      await request.promise;
    });

    expect(container.textContent).not.toContain("已同意隐私政策");
    expect(onAccept).not.toHaveBeenCalled();
    expect(localStorage.getItem("consent_id:code-1")).toBeNull();
    await act(async () => root.unmount());
  });
});
