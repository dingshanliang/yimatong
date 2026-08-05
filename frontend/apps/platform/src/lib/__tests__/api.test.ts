import { describe, expect, it, vi } from "vitest";

import api from "../api";

describe("platform api authentication boundary", () => {
  it("uses credentials without reading browser storage or injecting Bearer", async () => {
    const localGet = vi.spyOn(Storage.prototype, "getItem");
    const response = await api.get("/platform/dashboard", {
      adapter: async (config) => ({
        data: { ok: true },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      }),
    });

    expect(localGet).not.toHaveBeenCalled();
    expect(response.config.withCredentials).toBe(true);
    expect(response.config.withXSRFToken).toBe(true);
    expect(response.config.xsrfCookieName).toBe("platform_csrf_token");
    expect(response.config.xsrfHeaderName).toBe("X-Platform-CSRF");
    expect(response.config.headers.Authorization).toBeUndefined();
  });
});
