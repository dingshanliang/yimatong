import { describe, expect, it, vi } from "vitest";

import {
  TENANT_PLAN_EXPIRED_EVENT,
  isTenantPlanExpired,
  reportTenantPlanExpired,
  setTenantPlanReadOnly,
  tenantEntitlementKey,
  tenantPlanBlocksMethod,
} from "../plan-entitlement";

describe("tenant plan entitlement client contract", () => {
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

  it("blocks mutation methods while preserving read requests", () => {
    setTenantPlanReadOnly(true);
    expect(tenantPlanBlocksMethod("post")).toBe(true);
    expect(tenantPlanBlocksMethod("PATCH")).toBe(true);
    expect(tenantPlanBlocksMethod("get")).toBe(false);
    setTenantPlanReadOnly(false);
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
