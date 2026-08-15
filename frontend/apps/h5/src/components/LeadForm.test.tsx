import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { get, post } }));

import { LeadForm } from "./LeadForm";

const CONSUMER_ID = "018f0c86-4c11-7b31-aeb7-94a11b229fd0";
const RECEIPT_ID = "018f0c86-4c11-7b31-aeb7-94a11b229fd2";

function scanTokenFor(consumerId = CONSUMER_ID) {
  const encode = (value: object) =>
    btoa(JSON.stringify(value))
      .replace(/=/g, "")
      .replace(/\+/g, "-")
      .replace(/\//g, "_");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode({
    type: "scan_token",
    consumer_id: consumerId,
    exp: Math.floor(Date.now() / 1000) + 1800,
  })}.signature`;
}

describe("LeadForm consent authority", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    post.mockReset();
    get.mockReset();
    localStorage.clear();
    get.mockResolvedValue({
      data: {
        purpose: "lead_capture",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
      },
    });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    container.remove();
  });

  it("does not submit PII when the consent receipt cannot be persisted", async () => {
    post.mockRejectedValue(new Error("consent unavailable"));
    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[aria-label="同意隐私政策"]')
        ?.click();
    });

    await act(async () => {
      container.querySelector<HTMLFormElement>("form")?.requestSubmit();
    });

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/public/consents",
      expect.any(Object),
      expect.objectContaining({
        headers: { Authorization: "Bearer scan-token" },
      })
    );
    expect(container.textContent).not.toContain("提交成功");
    expect(container.textContent).toContain("提交失败");
    await act(async () => root.unmount());
  });

  it("threads the durable consent receipt into authoritative lead capture", async () => {
    post
      .mockResolvedValueOnce({
        data: { consent_id: RECEIPT_ID, status: "granted" },
      })
      .mockResolvedValueOnce({
        data: {
          consumer_id: CONSUMER_ID,
          scan_token: scanTokenFor(),
          status: "captured",
        },
      })
      .mockResolvedValueOnce({
        data: { consent_id: RECEIPT_ID, status: "withdrawn" },
      });
    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[aria-label="同意隐私政策"]')
        ?.click();
    });
    const phone = container.querySelector<HTMLInputElement>(
      'input[name="phone"]'
    );
    if (phone) phone.value = "13800138000";

    await act(async () => {
      container.querySelector<HTMLFormElement>("form")?.requestSubmit();
    });

    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        consent_id: RECEIPT_ID,
        phone: "13800138000",
        idempotency_key: expect.any(String),
      })
    );
    expect(post.mock.calls[1][1]).not.toHaveProperty("public_id");
    expect(container.textContent).toContain("已提交");
    expect(localStorage.getItem("consumer_id")).toBe(CONSUMER_ID);
    expect(localStorage.getItem("scan_token")).toBe(scanTokenFor());
    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(RECEIPT_ID);
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "撤回联系授权")
        ?.click();
    });
    expect(post.mock.calls[2][2]).toEqual({
      headers: { Authorization: `Bearer ${scanTokenFor()}` },
    });
    await act(async () => root.unmount());
  });

  it.each([
    {
      name: "non-captured status",
      response: {
        status: "pending",
        consumer_id: CONSUMER_ID,
        scan_token: scanTokenFor(),
      },
    },
    {
      name: "malformed consumer id",
      response: {
        status: "captured",
        consumer_id: "consumer-1",
        scan_token: scanTokenFor("consumer-1"),
      },
    },
    {
      name: "malformed scan token",
      response: {
        status: "captured",
        consumer_id: CONSUMER_ID,
        scan_token: "not-a-scan-token",
      },
    },
    {
      name: "scan token bound to another consumer",
      response: {
        status: "captured",
        consumer_id: CONSUMER_ID,
        scan_token: scanTokenFor("018f0c86-4c11-7b31-aeb7-94a11b229fd1"),
      },
    },
  ])(
    "does not persist identity from a $name 2xx response",
    async ({ response }) => {
      post
        .mockResolvedValueOnce({
          data: { consent_id: RECEIPT_ID, status: "granted" },
        })
        .mockResolvedValueOnce({ data: response });
      const root = createRoot(container);
      await act(async () => {
        root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
      });
      await act(async () => {
        container
          .querySelector<HTMLInputElement>('input[aria-label="同意隐私政策"]')
          ?.click();
        container.querySelector<HTMLFormElement>("form")?.requestSubmit();
      });

      expect(container.textContent).not.toContain("提交成功");
      expect(container.textContent).toContain("提交失败");
      expect(localStorage.getItem("consumer_id")).toBeNull();
      expect(localStorage.getItem("scan_token")).toBeNull();
      expect(localStorage.getItem("lead_consent_id:code-1")).toBeNull();
      await act(async () => root.unmount());
    }
  );

  it("does not fetch or grant consent without an explicit resolver scan token", async () => {
    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" />);
    });

    expect(get).not.toHaveBeenCalled();
    expect(
      container.querySelector<HTMLButtonElement>('button[type="submit"]')
        ?.disabled
    ).toBe(true);
    expect(post).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("does not capture a lead from a late grant after the scan token changes", async () => {
    let resolveGrant!: (value: unknown) => void;
    const pendingGrant = new Promise((resolve) => {
      resolveGrant = resolve;
    });
    post.mockReturnValue(pendingGrant);
    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="old-token" />);
    });
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[aria-label="同意隐私政策"]')
        ?.click();
    });
    const phone = container.querySelector<HTMLInputElement>(
      'input[name="phone"]'
    );
    if (phone) phone.value = "13800138000";
    await act(async () => {
      container.querySelector<HTMLFormElement>("form")?.requestSubmit();
    });
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="fresh-token" />);
    });
    await act(async () => {
      resolveGrant({ data: { consent_id: "stale", status: "granted" } });
      await pendingGrant;
    });

    expect(post).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("提交成功");
    await act(async () => root.unmount());
  });

  it("restores only an exact current lead consent receipt and can withdraw it", async () => {
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockResolvedValueOnce({
        data: {
          consent_id: RECEIPT_ID,
          status: "granted",
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
    post.mockResolvedValueOnce({
      data: { consent_id: RECEIPT_ID, status: "withdrawn" },
    });

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });

    expect(container.textContent).toContain("已提交");
    const withdraw = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "撤回联系授权"
    );
    await act(async () => withdraw?.click());

    expect(post).toHaveBeenCalledWith(
      `/public/consents/${RECEIPT_ID}/withdraw`,
      { idempotency_key: expect.any(String) },
      { headers: { Authorization: "Bearer scan-token" } }
    );
    expect(localStorage.getItem("lead_consent_id:code-1")).toBeNull();
    expect(container.querySelector("form")).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("retries a retryable lead receipt status and restores the submitted state", async () => {
    vi.useFakeTimers();
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockRejectedValueOnce({
        response: { status: 409, data: { detail: "consent_authority_retry" } },
      })
      .mockResolvedValueOnce({
        data: {
          consent_id: RECEIPT_ID,
          status: "granted",
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
    const root = createRoot(container);

    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(RECEIPT_ID);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(container.textContent).toContain("已提交");
    expect(
      get.mock.calls.filter(
        ([url]) => url === `/public/consents/${RECEIPT_ID}/status`
      )
    ).toHaveLength(2);
    await act(async () => root.unmount());
  });

  it("keeps the lead locator after the bounded retry budget is exhausted", async () => {
    vi.useFakeTimers();
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get.mockResolvedValueOnce({
      data: {
        purpose: "lead_capture",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
      },
    });
    get.mockRejectedValue({
      response: { status: 409, data: { detail: "consent_authority_retry" } },
    });
    const root = createRoot(container);

    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(RECEIPT_ID);
    expect(
      get.mock.calls.filter(
        ([url]) => url === `/public/consents/${RECEIPT_ID}/status`
      )
    ).toHaveLength(3);
    expect(container.textContent).not.toContain("已提交");
    await act(async () => root.unmount());
  });

  it.each([
    {
      name: "cross-subject denial",
      result: () =>
        Promise.reject({
          response: {
            status: 403,
            data: { detail: "consent_authority_denied" },
          },
        }),
    },
    {
      name: "malformed receipt",
      result: () =>
        Promise.resolve({
          data: {
            consent_id: RECEIPT_ID,
            status: "granted",
            purpose: "privacy_policy",
            policy_version: "v2",
            policy_digest: "a".repeat(64),
          },
        }),
    },
  ])("clears a stored locator after a $name", async ({ result }) => {
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get.mockResolvedValueOnce({
      data: {
        purpose: "lead_capture",
        policy_version: "v2",
        policy_digest: "a".repeat(64),
      },
    });
    get.mockImplementationOnce(result);

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });

    expect(localStorage.getItem("lead_consent_id:code-1")).toBeNull();
    expect(container.textContent).not.toContain("已提交");
    await act(async () => root.unmount());
  });

  it("keeps the receipt and submitted state when withdraw fails", async () => {
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockResolvedValueOnce({
        data: {
          consent_id: RECEIPT_ID,
          status: "granted",
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
    post.mockRejectedValueOnce(new Error("network"));

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    const withdraw = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "撤回联系授权"
    );
    await act(async () => withdraw?.click());

    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(RECEIPT_ID);
    expect(container.textContent).toContain("已提交");
    expect(container.textContent).toContain("撤回未完成");
    await act(async () => root.unmount());
  });

  it("ignores a stale receipt restore after the token changes", async () => {
    let resolveStatus!: (value: unknown) => void;
    const pendingStatus = new Promise((resolve) => {
      resolveStatus = resolve;
    });
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockReturnValueOnce(pendingStatus)
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockRejectedValueOnce({
        response: { status: 403, data: { detail: "consent_authority_denied" } },
      });

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="old-token" />);
    });
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="fresh-token" />);
    });
    await act(async () => {
      resolveStatus({
        data: {
          consent_id: RECEIPT_ID,
          status: "granted",
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
      await pendingStatus;
    });

    expect(container.textContent).not.toContain("已提交");
    await act(async () => root.unmount());
  });

  it("can grant and capture again after a durable withdrawal", async () => {
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    const replacementReceipt = "018f0c86-4c11-7b31-aeb7-94a11b229fd3";
    get
      .mockResolvedValueOnce({
        data: {
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      })
      .mockResolvedValueOnce({
        data: {
          consent_id: RECEIPT_ID,
          status: "granted",
          purpose: "lead_capture",
          policy_version: "v2",
          policy_digest: "a".repeat(64),
        },
      });
    post
      .mockResolvedValueOnce({
        data: { consent_id: RECEIPT_ID, status: "withdrawn" },
      })
      .mockResolvedValueOnce({
        data: { consent_id: replacementReceipt, status: "granted" },
      })
      .mockResolvedValueOnce({
        data: {
          consumer_id: CONSUMER_ID,
          scan_token: scanTokenFor(),
          status: "captured",
        },
      });

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="scan-token" />);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "撤回联系授权")
        ?.click();
    });
    await act(async () => {
      container
        .querySelector<HTMLInputElement>('input[aria-label="同意隐私政策"]')
        ?.click();
    });
    const phone = container.querySelector<HTMLInputElement>(
      'input[name="phone"]'
    );
    if (phone) phone.value = "13800138000";
    await act(async () => {
      container.querySelector<HTMLFormElement>("form")?.requestSubmit();
    });

    expect(post).toHaveBeenCalledTimes(3);
    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(
      replacementReceipt
    );
    expect(container.textContent).toContain("已提交");
    await act(async () => root.unmount());
  });

  it("does not let a stale withdraw completion clear the current token state", async () => {
    let resolveWithdraw!: (value: unknown) => void;
    const pendingWithdraw = new Promise((resolve) => {
      resolveWithdraw = resolve;
    });
    localStorage.setItem("lead_consent_id:code-1", RECEIPT_ID);
    const policy = {
      purpose: "lead_capture",
      policy_version: "v2",
      policy_digest: "a".repeat(64),
    };
    const receipt = {
      consent_id: RECEIPT_ID,
      status: "granted",
      ...policy,
    };
    get
      .mockResolvedValueOnce({ data: policy })
      .mockResolvedValueOnce({ data: receipt })
      .mockResolvedValueOnce({ data: policy })
      .mockResolvedValueOnce({ data: receipt });
    post.mockReturnValueOnce(pendingWithdraw);

    const root = createRoot(container);
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="old-token" />);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "撤回联系授权")
        ?.click();
    });
    await act(async () => {
      root.render(<LeadForm publicId="code-1" scanToken="fresh-token" />);
    });
    await act(async () => {
      resolveWithdraw({
        data: { consent_id: RECEIPT_ID, status: "withdrawn" },
      });
      await pendingWithdraw;
    });

    expect(localStorage.getItem("lead_consent_id:code-1")).toBe(RECEIPT_ID);
    expect(container.textContent).toContain("已提交");
    await act(async () => root.unmount());
  });
});
