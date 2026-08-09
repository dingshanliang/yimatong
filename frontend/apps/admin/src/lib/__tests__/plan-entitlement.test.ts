import { afterEach, describe, expect, it, vi } from "vitest";

import {
  TENANT_PLAN_EXPIRED_EVENT,
  isTenantPlanExpired,
  reportTenantPlanExpired,
  setTenantPlanReadOnly,
  tenantEntitlementKey,
  tenantFeatureEnabled,
  tenantPlanBlocksRequest,
} from "../plan-entitlement";

describe("tenant plan entitlement client contract", () => {
  afterEach(() => setTenantPlanReadOnly(false));

  it("uses the backend UTC instant without browser timezone conversion", () => {
    expect(
      isTenantPlanExpired(
        "2027-07-01T15:59:59.999999Z",
        Date.parse("2027-07-01T15:59:59Z")
      )
    ).toBe(false);
    expect(
      isTenantPlanExpired(
        "2027-07-01T15:59:59.999999Z",
        Date.parse("2027-07-01T16:00:00Z")
      )
    ).toBe(true);
  });

  it("broadcasts the stable backend error code centrally", () => {
    const listener = vi.fn();
    window.addEventListener(TENANT_PLAN_EXPIRED_EVENT, listener);
    reportTenantPlanExpired();
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener(TENANT_PLAN_EXPIRED_EVENT, listener);
  });

  it("blocks tenant mutations while preserving reads and exact recovery paths", () => {
    setTenantPlanReadOnly(true);
    expect(tenantPlanBlocksRequest("post", "/products")).toBe(true);
    expect(tenantPlanBlocksRequest("PATCH", "/tenants/me")).toBe(true);
    expect(tenantPlanBlocksRequest("get", "/products")).toBe(false);

    expect(tenantPlanBlocksRequest("post", "/auth/refresh")).toBe(false);
    expect(tenantPlanBlocksRequest("post", "/auth/logout")).toBe(false);
    expect(tenantPlanBlocksRequest("post", "/auth/change-password")).toBe(
      false
    );
    expect(tenantPlanBlocksRequest("post", "/agency/exit-context")).toBe(false);
    expect(
      tenantPlanBlocksRequest("post", "/api/v1/auth/logout?source=menu")
    ).toBe(false);

    expect(
      tenantPlanBlocksRequest(
        "post",
        "https://untrusted.example/api/v1/auth/logout"
      )
    ).toBe(true);
    expect(tenantPlanBlocksRequest("put", "/auth/logout")).toBe(true);
    expect(tenantPlanBlocksRequest("post", "/auth/logout-all")).toBe(true);
    expect(tenantPlanBlocksRequest("post", "/agency/switch-context")).toBe(
      true
    );
  });

  it("fails closed for paid features when the entitlement payload is absent", () => {
    expect(tenantFeatureEnabled(undefined, "ai_assistant")).toBe(false);
    expect(tenantFeatureEnabled(null, "risk_module")).toBe(false);
    expect(
      tenantFeatureEnabled({ channel_portal: true }, "channel_portal")
    ).toBe(true);
    expect(tenantFeatureEnabled({ white_label: false }, "white_label")).toBe(
      false
    );
  });

  it("uses a distinct live entitlement cache entry for each acting client", () => {
    expect(tenantEntitlementKey(null)).toBe(
      "/tenants/me/entitlement?context=self"
    );
    expect(tenantEntitlementKey("client-a")).toBe(
      "/tenants/me/entitlement?context=client-a"
    );
    expect(tenantEntitlementKey("client-b")).not.toBe(
      tenantEntitlementKey("client-a")
    );
  });
});
