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

function request(pathname: string, payload?: Record<string, unknown>) {
  return new NextRequest(`http://localhost${pathname}`, {
    headers: payload ? { cookie: `access_token=${jwt(payload)}` } : undefined,
  });
}

describe("admin middleware password-change gate", () => {
  it("allows invited customers to open registration without a session", () => {
    const response = middleware(request("/register?invite_code=INVITE123"));

    expect(response.status).toBe(200);
  });

  it("does not let a stale cookie block the public registration link", () => {
    const response = middleware(
      new NextRequest("http://localhost/register?invite_code=INVITE123", {
        headers: { cookie: "access_token=invalid-token" },
      })
    );

    expect(response.status).toBe(200);
  });

  it("redirects every other page to change-password for temporary credentials", () => {
    const response = middleware(
      request("/login", {
        sub: "account-1",
        tenant_id: "tenant-1",
        role: "operator",
        must_change_password: true,
      })
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost/change-password"
    );
  });

  it("does not allow a normal session to reopen the forced password page", () => {
    const response = middleware(
      request("/change-password", {
        sub: "account-1",
        tenant_id: "tenant-1",
        role: "operator",
        must_change_password: false,
      })
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/");
  });
});

describe("admin middleware agency scope gate", () => {
  const actingPayload = {
    sub: "agency-account",
    tenant_id: "agency-tenant",
    tenant_type: "agency",
    role: "operator",
    acting_tenant_id: "client-tenant",
    scope: ["products", "pages"],
  };

  it("allows a route covered by the acting scope", () => {
    expect(middleware(request("/products/p1", actingPayload)).status).toBe(200);
  });

  it("redirects an out-of-scope route before the page can issue API requests", () => {
    const response = middleware(request("/campaigns", actingPayload));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/products");
  });

  it("fails closed when an acting token has no usable scope", () => {
    const response = middleware(
      request("/products", { ...actingPayload, scope: [] })
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/agency");
  });

  it("allows imports only from an acting products workspace", () => {
    expect(middleware(request("/imports", actingPayload)).status).toBe(200);

    const codesOnly = middleware(
      request("/imports", { ...actingPayload, scope: ["codes"] })
    );
    expect(codesOnly.status).toBe(307);
    expect(codesOnly.headers.get("location")).toBe("http://localhost/codes");
  });

  it("redirects a base agency before the imports page can mount", () => {
    const response = middleware(
      request("/imports", {
        sub: "agency-account",
        tenant_id: "agency-tenant",
        tenant_type: "agency",
        role: "admin",
      })
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/agency");
  });
});

describe("admin middleware export audit gate", () => {
  it("allows only a base brand administrator to mount export audit", () => {
    const brandAdmin = {
      sub: "brand-admin",
      tenant_id: "brand-tenant",
      tenant_type: "brand",
      role: "admin",
    };
    expect(middleware(request("/exports", brandAdmin)).status).toBe(200);

    for (const [payload, destination] of [
      [{ ...brandAdmin, role: "viewer" }, "/"],
      [{ ...brandAdmin, role: "operator" }, "/"],
      [
        {
          sub: "agency-admin",
          tenant_id: "agency-tenant",
          tenant_type: "agency",
          role: "admin",
          acting_tenant_id: "brand-tenant",
          scope: ["codes"],
        },
        "/codes",
      ],
    ]) {
      const response = middleware(request("/exports", payload));
      expect(response.status).toBe(307);
      expect(response.headers.get("location")).toBe(
        `http://localhost${destination}`
      );
    }
  });
});

describe("admin middleware catalog role gate", () => {
  it.each(["/products", "/batches", "/imports"])(
    "redirects a viewer before %s can mount",
    (pathname) => {
      const response = middleware(
        request(pathname, {
          sub: "viewer-1",
          tenant_id: "tenant-1",
          tenant_type: "brand",
          role: "viewer",
        })
      );

      expect(response.status).toBe(307);
      expect(response.headers.get("location")).toBe("http://localhost/");
    }
  );

  it.each(["admin", "operator"])(
    "allows a brand %s to open catalog routes",
    (role) => {
      const response = middleware(
        request("/skus/sku-1", {
          sub: `${role}-1`,
          tenant_id: "tenant-1",
          tenant_type: "brand",
          role,
        })
      );

      expect(response.status).toBe(200);
    }
  );

  it("allows an acting agency with products scope to open batches", () => {
    const response = middleware(
      request("/batches", {
        sub: "agency-operator",
        tenant_id: "agency-tenant",
        tenant_type: "agency",
        role: "operator",
        acting_tenant_id: "client-tenant",
        scope: ["products"],
      })
    );

    expect(response.status).toBe(200);
  });
});

describe("admin middleware channel role gate", () => {
  it("allows brand admin and operator but rejects viewer direct navigation", () => {
    expect(
      middleware(request("/channels", { tenant_type: "brand", role: "admin" }))
        .status
    ).toBe(200);
    expect(
      middleware(
        request("/channels", { tenant_type: "brand", role: "operator" })
      ).status
    ).toBe(200);
    const viewer = middleware(
      request("/channels", { tenant_type: "brand", role: "viewer" })
    );
    expect(viewer.status).toBe(307);
    expect(viewer.headers.get("location")).toBe("http://localhost/");
  });
});

describe("admin middleware risk role gate", () => {
  it.each(["/risk", "/risk-center", "/risk-dashboard"])(
    "stops viewer navigation to %s before the page can issue requests",
    (pathname) => {
      const response = middleware(
        request(pathname, { tenant_type: "brand", role: "viewer" })
      );
      expect(response.status).toBe(307);
      expect(response.headers.get("location")).toBe("http://localhost/");
    }
  );

  it.each(["admin", "operator"])(
    "allows direct brand %s risk access",
    (role) => {
      expect(
        middleware(request("/risk", { tenant_type: "brand", role })).status
      ).toBe(200);
    }
  );

  it("rejects an acting agency even when its scope contains campaigns", () => {
    const response = middleware(
      request("/risk", {
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: "brand-tenant",
        scope: ["campaigns"],
      })
    );
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost/");
  });
});
