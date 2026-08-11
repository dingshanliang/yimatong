import { describe, expect, it } from "vitest";
import {
  canManageAgencyAuthorizations,
  firstAgencyScopedRoute,
  isAgencyScopedRouteAllowed,
  takeoverAccessForPrincipal,
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
    expect(isAgencyScopedRouteAllowed("/imports", ["products"])).toBe(true);
    expect(isAgencyScopedRouteAllowed("/imports", ["codes"])).toBe(false);
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

describe("takeoverAccessForPrincipal", () => {
  it("grants the exact brand role matrix", () => {
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "brand",
        role: "admin",
        acting_tenant_id: null,
        agency_scope: null,
      })
    ).toEqual({
      canRead: true,
      canPrepare: true,
      canApprove: true,
      canExecute: true,
      canRollback: true,
      canAudit: true,
    });
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "brand",
        role: "operator",
        acting_tenant_id: null,
        agency_scope: null,
      })
    ).toEqual({
      canRead: true,
      canPrepare: true,
      canApprove: false,
      canExecute: false,
      canRollback: false,
      canAudit: true,
    });
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "brand",
        role: "viewer",
        acting_tenant_id: null,
        agency_scope: null,
      }).canRead
    ).toBe(false);
  });

  it("allows acting codes roles but rejects base agencies", () => {
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: "client-1",
        agency_scope: ["codes"],
      }).canRollback
    ).toBe(true);
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "agency",
        role: "operator",
        acting_tenant_id: "client-1",
        agency_scope: ["codes"],
      })
    ).toMatchObject({
      canRead: true,
      canPrepare: true,
      canApprove: false,
      canExecute: false,
      canRollback: false,
      canAudit: true,
    });
    expect(
      takeoverAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: null,
        agency_scope: null,
      }).canRead
    ).toBe(false);
  });
});
