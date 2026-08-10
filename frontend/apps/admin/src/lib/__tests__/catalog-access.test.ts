import { describe, expect, it } from "vitest";

import { catalogAccessForPrincipal } from "../catalog-access";

describe("catalogAccessForPrincipal", () => {
  it.each([
    ["admin", true, true, true, true],
    ["operator", true, true, false, false],
    ["viewer", false, false, false, false],
  ] as const)(
    "maps brand %s to the backend catalog contract",
    (role, canRead, canWrite, canDelete, canRecall) => {
      expect(
        catalogAccessForPrincipal({
          tenant_type: "brand",
          role,
          acting_tenant_id: null,
          agency_scope: null,
        })
      ).toEqual({ canRead, canWrite, canDelete, canRecall });
    }
  );

  it("requires an acting client and the products scope for an agency principal", () => {
    expect(
      catalogAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: null,
        agency_scope: ["products"],
      })
    ).toEqual({
      canRead: false,
      canWrite: false,
      canDelete: false,
      canRecall: false,
    });
    expect(
      catalogAccessForPrincipal({
        tenant_type: "agency",
        role: "operator",
        acting_tenant_id: "client-1",
        agency_scope: ["pages"],
      })
    ).toEqual({
      canRead: false,
      canWrite: false,
      canDelete: false,
      canRecall: false,
    });
    expect(
      catalogAccessForPrincipal({
        tenant_type: "agency",
        role: "operator",
        acting_tenant_id: "client-1",
        agency_scope: ["products"],
      })
    ).toEqual({
      canRead: true,
      canWrite: true,
      canDelete: false,
      canRecall: false,
    });
    expect(
      catalogAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: "client-1",
        agency_scope: ["products"],
      })
    ).toEqual({
      canRead: true,
      canWrite: true,
      canDelete: true,
      canRecall: false,
    });
  });
});
