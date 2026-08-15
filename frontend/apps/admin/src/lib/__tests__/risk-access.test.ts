import { describe, expect, it } from "vitest";
import { riskAccessForPrincipal } from "../risk-access";

describe("riskAccessForPrincipal", () => {
  it.each(["admin", "operator"])("allows direct brand %s", (role) => {
    expect(riskAccessForPrincipal({ tenant_type: "brand", role })).toEqual({
      canRead: true,
      canManage: true,
      canEvaluate: true,
    });
  });

  it.each([
    { tenant_type: "brand", role: "viewer" },
    { tenant_type: "brand", role: "distributor" },
    { tenant_type: "brand", role: "store_guide" },
    { tenant_type: "agency", role: "admin" },
    {
      tenant_type: "agency",
      role: "admin",
      acting_tenant_id: "tenant-1",
    },
  ])("denies $tenant_type/$role and acting principals", (principal) => {
    expect(riskAccessForPrincipal(principal)).toEqual({
      canRead: false,
      canManage: false,
      canEvaluate: false,
    });
  });
});
