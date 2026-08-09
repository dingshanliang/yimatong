import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockPost } = vi.hoisted(() => ({ mockPost: vi.fn() }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    default: { post: mockPost },
    registerAuthInterceptorHandlers: vi.fn(),
  };
});

import { useAuthStore } from "../auth";
import { AgencyContextRevalidationError } from "../api";

function jwt(payload: Record<string, unknown>) {
  const encode = (value: Record<string, unknown>) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.signature`;
}

describe("useAuthStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockReset();
    localStorage.clear();
    useAuthStore.setState({ user: null, token: null, loading: false });
  });

  it("revalidates the live acting tenant before returning a replay token", async () => {
    const previousToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
      acting_tenant_id: "client-1",
      scope: ["products"],
    });
    const baseToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
    });
    const actingToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
      acting_tenant_id: "client-1",
      scope: ["products"],
    });
    localStorage.setItem("access_token", previousToken);
    localStorage.setItem("refresh_token", "old-refresh-token");
    localStorage.setItem(
      "auth_store",
      JSON.stringify({
        account_id: "account-1",
        tenant_id: "agency-1",
        role: "operator",
        tenant_type: "agency",
        email: "agency@test.com",
        name: "代运营",
        acting_tenant_id: "client-1",
        agency_scope: ["products"],
        must_change_password: false,
      })
    );
    mockPost
      .mockResolvedValueOnce({
        data: { access_token: baseToken },
      })
      .mockResolvedValueOnce({
        data: {
          access_token: actingToken,
          acting_tenant_id: "client-1",
          scope: ["products"],
        },
      });

    await expect(useAuthStore.getState().silentRefresh()).resolves.toBe(
      actingToken
    );

    expect(mockPost).toHaveBeenNthCalledWith(
      1,
      "/auth/refresh",
      { refresh_token: "old-refresh-token" },
      {
        headers: { "X-Auth-Delivery": "cookie" },
        skipAuthRefresh: true,
      }
    );
    expect(mockPost).toHaveBeenNthCalledWith(
      2,
      "/agency/switch-context",
      { client_tenant_id: "client-1" },
      {
        headers: { Authorization: `Bearer ${baseToken}` },
        skipAuthRefresh: true,
      }
    );
    expect(localStorage.getItem("access_token")).toBe(actingToken);
    expect(useAuthStore.getState().user).toMatchObject({
      acting_tenant_id: "client-1",
      agency_scope: ["products"],
    });
  });

  it("exits acting context and rejects replay when live authorization is gone", async () => {
    const previousToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
      acting_tenant_id: "client-1",
    });
    const baseToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
    });
    localStorage.setItem("access_token", previousToken);
    localStorage.setItem("refresh_token", "old-refresh-token");
    localStorage.setItem(
      "auth_store",
      JSON.stringify({
        account_id: "account-1",
        tenant_id: "agency-1",
        role: "operator",
        tenant_type: "agency",
        email: "agency@test.com",
        name: "代运营",
        acting_tenant_id: "client-1",
        agency_scope: ["products"],
        must_change_password: false,
      })
    );
    mockPost
      .mockResolvedValueOnce({
        data: { access_token: baseToken },
      })
      .mockRejectedValueOnce(new Error("403"));

    await expect(
      useAuthStore.getState().silentRefresh()
    ).rejects.toBeInstanceOf(AgencyContextRevalidationError);

    expect(localStorage.getItem("access_token")).toBe(baseToken);
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(useAuthStore.getState().user).toMatchObject({
      acting_tenant_id: null,
      agency_scope: null,
    });
  });

  it("trusts the refreshed JWT and clears stale agency context", async () => {
    localStorage.setItem(
      "auth_store",
      JSON.stringify({
        account_id: "account-1",
        tenant_id: "agency-1",
        role: "operator",
        tenant_type: "agency",
        email: "agency@test.com",
        name: "代运营",
        acting_tenant_id: "stale-client",
        agency_scope: ["analytics"],
        must_change_password: false,
      })
    );
    const accessToken = jwt({
      sub: "account-1",
      tenant_id: "agency-1",
      role: "operator",
      tenant_type: "agency",
      must_change_password: true,
    });
    mockPost.mockResolvedValue({
      data: {
        access_token: accessToken,
        expires_in: 900,
      },
    });

    await useAuthStore.getState().silentRefresh();

    expect(useAuthStore.getState().user).toMatchObject({
      acting_tenant_id: null,
      agency_scope: null,
      must_change_password: true,
    });
  });

  it("submits the refresh token before clearing the local session on logout", async () => {
    localStorage.setItem("access_token", "access-token");
    localStorage.setItem("refresh_token", "refresh-token");
    localStorage.setItem("auth_store", "{}");
    mockPost.mockResolvedValue({ data: { status: "ok" } });

    await useAuthStore.getState().logout();

    expect(mockPost).toHaveBeenCalledWith(
      "/auth/logout",
      { refresh_token: "refresh-token" },
      { skipAuthRefresh: true }
    );
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });

  it("retains the local session when logout cannot reach the server", async () => {
    localStorage.setItem("access_token", "access-token");
    localStorage.setItem("auth_store", "{}");
    useAuthStore.setState({ token: "access-token" });
    mockPost.mockRejectedValue(new Error("network unavailable"));

    await expect(useAuthStore.getState().logout()).rejects.toThrow(
      "network unavailable"
    );

    expect(localStorage.getItem("access_token")).toBe("access-token");
    expect(localStorage.getItem("auth_store")).toBe("{}");
    expect(useAuthStore.getState().token).toBe("access-token");
  });

  it("clears the browser session after a server-side partial logout", async () => {
    localStorage.setItem("access_token", "access-token");
    localStorage.setItem("auth_store", "{}");
    useAuthStore.setState({ token: "access-token" });
    mockPost.mockRejectedValue(
      Object.assign(new Error("partial logout"), {
        isAxiosError: true,
        response: { data: { code: "LOGOUT_PARTIAL" } },
      })
    );

    await expect(useAuthStore.getState().logout()).rejects.toThrow(
      "partial logout"
    );

    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("auth_store")).toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
  });
});
