import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResolveContent } from "./ResolveContent";

const payload = {
  scan_token: "scan-token",
  code_data: {
    public_id: "PK-REVISIT",
    status: "activated",
    lifecycle: "active",
    product: { name: "安心大米", origin: "产地" },
    batch: { batch_code: "PB-OK", origin: "产地", status: "active" },
  },
  scan_info: {},
  page_config: {
    modules: [{ id: "trace", type: "light_traceability", enabled: true }],
  },
};

describe("回访恢复入口（kc6d.7）", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    const storage = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, String(value)),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, String(value)),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  function render() {
    const root = createRoot(container);
    act(() => {
      root.render(
        <ResolveContent
          mode="json"
          publicId="PK-REVISIT"
          jsonPayload={payload}
          htmlContent={null}
        />
      );
    });
    return root;
  }

  it("本会话该码有领取记录时展示入口，链接携带 claim 与码标识", () => {
    sessionStorage.setItem(
      "yimatong:claim-revisit:latest:PK-REVISIT",
      "claim-9"
    );
    const root = render();

    const link = container.querySelector<HTMLAnchorElement>(
      'a[href*="/redpacket/result"]'
    );
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe(
      "/redpacket/result?claim_id=claim-9&public_id=PK-REVISIT"
    );
    expect(link?.textContent).toContain("查看我的红包");
    act(() => root.unmount());
  });

  it("无领取记录时不渲染入口", () => {
    const root = render();

    expect(container.querySelector('a[href*="/redpacket/result"]')).toBeNull();
    act(() => root.unmount());
  });

  it("其他码的领取记录不在本码展示", () => {
    sessionStorage.setItem("yimatong:claim-revisit:latest:PK-OTHER", "claim-8");
    const root = render();

    expect(container.querySelector('a[href*="/redpacket/result"]')).toBeNull();
    act(() => root.unmount());
  });
});
