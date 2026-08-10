import { describe, expect, it } from "vitest";

import { codeAccessForPrincipal } from "../code-access";

describe("codeAccessForPrincipal", () => {
  it.each([
    ["admin", true, true, true, true],
    ["operator", true, true, true, false],
    ["viewer", true, false, false, false],
  ])(
    "maps brand %s to the backend code permissions",
    (role, canRead, canGenerate, canExport, canManage) => {
      expect(codeAccessForPrincipal({ tenant_type: "brand", role })).toEqual({
        canRead,
        canGenerate,
        canExport,
        canManage,
      });
    }
  );

  it("blocks a base agency until it enters an authorized client context", () => {
    expect(
      codeAccessForPrincipal({ tenant_type: "agency", role: "admin" })
    ).toEqual({
      canRead: false,
      canGenerate: false,
      canExport: false,
      canManage: false,
    });
  });

  it.each([
    ["admin", true],
    ["operator", false],
  ])(
    "grants an acting %s only the role capabilities inside codes scope",
    (role, canManage) => {
      expect(
        codeAccessForPrincipal({
          tenant_type: "agency",
          role,
          acting_tenant_id: "client-1",
          agency_scope: ["codes"],
        })
      ).toEqual({
        canRead: true,
        canGenerate: true,
        canExport: true,
        canManage,
      });
    }
  );

  it("fails closed for an acting agency outside codes scope", () => {
    expect(
      codeAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: "client-1",
        agency_scope: ["products"],
      })
    ).toEqual({
      canRead: false,
      canGenerate: false,
      canExport: false,
      canManage: false,
    });
  });
});
