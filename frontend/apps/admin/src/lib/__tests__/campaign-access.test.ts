import { describe, expect, it } from "vitest";

import type { AuthUser } from "../auth";
import { resolveCampaignAccess } from "../campaign-access";

function user(patch: Partial<AuthUser>): AuthUser {
  return {
    account_id: "account-1",
    tenant_id: "tenant-1",
    role: "admin",
    tenant_type: "brand",
    email: "admin@example.com",
    name: "Admin",
    acting_tenant_id: null,
    agency_scope: null,
    must_change_password: false,
    ...patch,
  };
}

describe("campaign access", () => {
  it("allows brand admins and operators", () => {
    expect(resolveCampaignAccess(user({ role: "admin" })).canManage).toBe(true);
    expect(resolveCampaignAccess(user({ role: "operator" })).canManage).toBe(
      true
    );
  });

  it("denies viewers and base-agency sessions", () => {
    expect(resolveCampaignAccess(user({ role: "viewer" })).canView).toBe(false);
    expect(resolveCampaignAccess(user({ tenant_type: "agency" })).canView).toBe(
      false
    );
  });

  it("requires a live acting client and campaigns scope", () => {
    expect(
      resolveCampaignAccess(
        user({
          tenant_type: "agency",
          acting_tenant_id: "client-1",
          agency_scope: ["campaigns"],
        })
      ).canManage
    ).toBe(true);
    expect(
      resolveCampaignAccess(
        user({
          tenant_type: "agency",
          acting_tenant_id: "client-1",
          agency_scope: ["products"],
        })
      ).canView
    ).toBe(false);
  });
});
