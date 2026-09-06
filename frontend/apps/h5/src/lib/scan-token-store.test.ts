import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearActiveScanToken,
  readActiveScanToken,
  saveScanToken,
  setScanTokenScope,
} from "./scan-token-store";

describe("scan-token-store per-code isolation", () => {
  let storage: Map<string, string>;

  beforeEach(() => {
    storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, String(value)),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stores and reads the token under the active code's own key", () => {
    setScanTokenScope("code-a");
    saveScanToken("code-a", "token-a");

    expect(readActiveScanToken()).toBe("token-a");
    expect(storage.get("yimatong:scan-token:code-a")).toBe("token-a");
    expect(storage.has("scan_token")).toBe(false);
  });

  it("never leaks another code's token through the implicit scope", () => {
    setScanTokenScope("code-a");
    saveScanToken("code-a", "token-a");

    setScanTokenScope("code-b");
    expect(readActiveScanToken()).toBeNull();

    clearActiveScanToken();
    expect(storage.get("yimatong:scan-token:code-a")).toBe("token-a");
  });

  it("returns null and no-ops without an active scope", () => {
    expect(readActiveScanToken()).toBeNull();
    expect(() => clearActiveScanToken()).not.toThrow();
    expect(storage.has("yimatong:scan-token:code-a")).toBe(false);
  });

  it("purges the legacy global scan_token key when a scope is set", () => {
    storage.set("scan_token", "stale-global-token");
    setScanTokenScope("code-a");
    expect(storage.has("scan_token")).toBe(false);
  });

  it("ignores empty tokens on save", () => {
    setScanTokenScope("code-a");
    saveScanToken("code-a", "");
    expect(storage.has("yimatong:scan-token:code-a")).toBe(false);
  });
});
