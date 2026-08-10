import { describe, expect, it } from "vitest";
import {
  canManageAgencyAuthorizations,
  firstAgencyScopedRoute,
  isAgencyScopedRouteAllowed,
} from "../agency-access";

describe("canManageAgencyAuthorizations", () => {
  it("allows only brand administrators", () => {
    expect(
      canManageAgencyAuthorizations({ tenant_type: "brand", role: "admin" })
    ).toBe(true);
    expect(
      canManageAgencyAuthorizations({ tenant_type: "brand", role: "operator" })
    ).toBe(false);
    expect(
      canManageAgencyAuthorizations({ tenant_type: "brand", role: "viewer" })
    ).toBe(false);
    expect(
      canManageAgencyAuthorizations({ tenant_type: "agency", role: "admin" })
    ).toBe(false);
  });

  it("maps acting routes to the exact granted scope", () => {
    expect(isAgencyScopedRouteAllowed("/products/p1", ["products"])).toBe(true);
    expect(isAgencyScopedRouteAllowed("/campaigns", ["products"])).toBe(false);
    expect(isAgencyScopedRouteAllowed("/settings/tenant", ["products"])).toBe(
      false
    );
  });

  it("chooses a stable first route for an acting workspace", () => {
    expect(firstAgencyScopedRoute(["pages", "products"])).toBe("/products");
    expect(firstAgencyScopedRoute(["analytics", "products"])).toBe("/");
    expect(firstAgencyScopedRoute([])).toBe("/agency");
  });
});
