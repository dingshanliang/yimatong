import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FallbackError } from "@/components/FallbackError";
import { ErrorPage } from "@/components/ErrorPage";
import { CodePageClient } from "./CodePageClient";

vi.mock("./ResolveContent", async () => {
  const { FallbackError: RealFallbackError } =
    await import("@/components/FallbackError");
  return {
    ResolveContent: ({
      jsonPayload,
      onRetry,
      retrying,
    }: {
      jsonPayload: Record<string, unknown> | null;
      onRetry?: () => void;
      retrying?: boolean;
    }) =>
      jsonPayload ? (
        <div>RESOLVED</div>
      ) : (
        <RealFallbackError onRetry={onRetry} retrying={retrying} />
      ),
  };
});

function okResponse() {
  return {
    ok: true,
    json: async () => ({ code_data: { result: "verified" }, scan_info: {} }),
  };
}

describe("CodePageClient scan retry", () => {
  let container: HTMLDivElement;
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    // 并行文件可能破坏全局 storage：本文件自建隔离的 localStorage
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, String(value)),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    container.remove();
  });

  async function render() {
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <CodePageClient
          publicId="pk-1"
          apiBase="https://api.test"
          retryDelayMs={5}
        />
      );
    });
    return root;
  }

  /** 在 act 内推进假时钟，让组件的重试定时器与微任务确定性落地。 */
  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it("auto-retries once and recovers from a transient failure", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue(okResponse());
    const root = await render();
    await advance(20);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("RESOLVED");
    await act(async () => root.unmount());
  });

  it("manual retry keeps a disabled loading button until the result lands", async () => {
    fetchMock.mockRejectedValue(new Error("network"));
    const root = await render();
    await advance(20);

    let resolveFetch!: (value: unknown) => void;
    fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      })
    );
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });

    // 重试进行中：按钮禁用并显示加载文案，重复点击不再发请求
    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.disabled).toBe(true);
    expect(button?.textContent).toContain("正在查验");
    await act(async () => {
      button?.click();
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await act(async () => {
      resolveFetch(okResponse());
      await Promise.resolve();
    });
    await advance(20);
    expect(container.textContent).toContain("RESOLVED");
    await act(async () => root.unmount());
  });

  it("falls to the retry surface only after the auto retry also fails", async () => {
    fetchMock.mockRejectedValue(new Error("network"));
    const root = await render();
    await advance(20);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("暂时无法加载");
    expect(container.textContent).toContain("重新查验");
    // 自动重试已用尽后不再自行发起新请求
    await advance(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
  });

  it("manual retry refetches and recovers", async () => {
    fetchMock.mockRejectedValue(new Error("network"));
    const root = await render();
    await advance(20);

    fetchMock.mockResolvedValue(okResponse());
    await act(async () => {
      container.querySelector<HTMLButtonElement>("button")?.click();
    });
    await advance(20);

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(container.textContent).toContain("RESOLVED");
    await act(async () => root.unmount());
  });
});

describe("FallbackError retry affordance", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("shows no retry button without a handler", () => {
    const root = createRoot(container);
    act(() => {
      root.render(<FallbackError />);
    });
    expect(container.querySelector("button")).toBeNull();
    act(() => root.unmount());
  });

  it("disables the retry button while retrying", () => {
    const root = createRoot(container);
    act(() => {
      root.render(<FallbackError onRetry={() => {}} retrying />);
    });
    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("正在查验");
    expect(button?.disabled).toBe(true);
    act(() => root.unmount());
  });
});

describe("ErrorPage business terminal", () => {
  it("hides the retry button for terminal code states", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const root = createRoot(container);
    act(() => {
      root.render(
        <ErrorPage errorCode="revoked" publicId="pk-1" showRetry={false} />
      );
    });
    expect(container.querySelector("button")).toBeNull();
    expect(container.textContent).toContain("客服");
    act(() => root.unmount());
    vi.unstubAllGlobals();
    container.remove();
  });
});
