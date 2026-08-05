import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { middleware } from "../middleware";

function jwt(payload: Record<string, unknown>) {
  const encode = (value: Record<string, unknown>) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.signature`;
}

describe("platform middleware", () => {
  it("rejects a tenant admin token", () => {
    const request = new NextRequest("http://localhost/tenants", {
      headers: {
        cookie: `platform_access_token=${jwt({
          sub: "tenant-admin",
          tenant_id: "tenant-1",
          role: "admin",
          tenant_type: "brand",
        })}`,
      },
    });

    const response = middleware(request);

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/login");
  });

  it("does not accept the legacy tenant access cookie", () => {
    const request = new NextRequest("http://localhost/tenants", {
      headers: {
        cookie: `access_token=${jwt({
          sub: "platform-admin",
          tenant_id: "platform",
          role: "platform_admin",
          tenant_type: "platform",
        })}`,
      },
    });

    expect(middleware(request).status).toBe(307);
  });
});
