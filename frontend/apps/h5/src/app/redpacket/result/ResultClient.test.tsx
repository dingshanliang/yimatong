import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
const { searchParamsRef } = vi.hoisted(() => ({
  searchParamsRef: { current: "" },
}));
vi.mock("@/lib/api", () => ({ apiClient: { get } }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsRef.current),
}));

import { RedPacketResultClient } from "./ResultClient";

function statusResponse(
  status: "processing" | "success" | "failed",
  extra: Record<string, unknown> = {}
) {
  return {
    data: {
      status,
      amount_minor: null,
      completed_at: null,
      failure_reason: null,
      ...extra,
    },
  };
}

describe("RedPacketResultClient polling", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    get.mockReset();
    window.sessionStorage.clear();
    searchParamsRef.current = "claim_id=claim-1";
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    container.remove();
  });

  async function renderClient() {
    const root = createRoot(container);
    await act(async () => {
      root.render(<RedPacketResultClient />);
    });
    return root;
  }

  it("renders processing with neutral copy while awaiting the terminal state", async () => {
    get.mockResolvedValue(statusResponse("processing"));
    const root = await renderClient();

    expect(container.textContent).toContain("处理中");
    expect(container.textContent).toContain("通常几分钟内完成");
    // 处理中文案不得承诺固定到账时长
    expect(container.textContent).not.toContain("1-3 分钟");
    await act(async () => root.unmount());
  });

  it("stops polling and shows the amount once delivered", async () => {
    get.mockResolvedValueOnce(statusResponse("processing"));
    get.mockResolvedValueOnce(
      statusResponse("success", {
        amount_minor: 880,
        completed_at: "2026-08-15T12:00:00+00:00",
      })
    );
    const root = await renderClient();
    expect(get).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(get).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("领取成功");
    expect(container.textContent).toContain("8.8");
    // 终态后不再轮询
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(get).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
  });

  it("shows failure reason category with a support path", async () => {
    get.mockResolvedValue(
      statusResponse("failed", { failure_reason: "recipient_missing" })
    );
    const root = await renderClient();

    expect(container.textContent).toContain("红包未发出");
    expect(container.textContent).toContain("微信授权信息缺失");
    expect(container.textContent).toContain("联系活动客服");
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it("stops with an unavailable state when the query is rejected", async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    const root = await renderClient();

    expect(container.textContent).toContain("暂时无法查询结果");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it("keeps retrying on transient errors instead of faking a terminal state", async () => {
    get.mockRejectedValueOnce(new Error("network"));
    get.mockResolvedValueOnce(statusResponse("success", { amount_minor: 100 }));
    const root = await renderClient();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(get).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("领取成功");
    await act(async () => root.unmount());
  });

  it("caps polling at the deadline and stays honestly processing", async () => {
    get.mockResolvedValue(statusResponse("processing"));
    const root = await renderClient();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(16 * 60 * 1000);
    });

    expect(container.textContent).toContain("仍在处理中");
    expect(container.textContent).not.toContain("领取成功");
    expect(container.textContent).not.toContain("红包未发出");
    const callsAtCap = get.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(get.mock.calls.length).toBe(callsAtCap);
    await act(async () => root.unmount());
  });

  it("is unavailable immediately without a claim id", async () => {
    searchParamsRef.current = "";
    const root = await renderClient();

    expect(container.textContent).toContain("暂时无法查询结果");
    expect(get).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("sends the stored revisit credential as the bearer token", async () => {
    window.sessionStorage.setItem("yimatong:claim-revisit:claim-1", "cred-1");
    get.mockResolvedValue(statusResponse("processing"));
    const root = await renderClient();

    expect(get).toHaveBeenCalledWith("/benefit-claims/claim-1/status", {
      headers: { Authorization: "Bearer cred-1" },
    });
    await act(async () => root.unmount());
  });
});
