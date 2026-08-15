import { describe, expect, it } from "vitest";
import { channelAccessForPrincipal } from "../channel-access";

describe("channelAccessForPrincipal", () => {
  it.each([
    [
      "admin",
      { canRead: true, canManage: true, canAllocate: true, canScope: true },
    ],
    [
      "operator",
      { canRead: true, canManage: true, canAllocate: true, canScope: false },
    ],
    [
      "viewer",
      { canRead: false, canManage: false, canAllocate: false, canScope: false },
    ],
  ])("maps brand %s", (role, expected) => {
    expect(channelAccessForPrincipal({ tenant_type: "brand", role })).toEqual(
      expected
    );
  });

  it("fails closed for acting agencies and unsupported principals", () => {
    expect(
      channelAccessForPrincipal({
        tenant_type: "agency",
        role: "admin",
        acting_tenant_id: "brand-1",
      })
    ).toEqual({
      canRead: false,
      canManage: false,
      canAllocate: false,
      canScope: false,
    });
    expect(channelAccessForPrincipal(null).canRead).toBe(false);
  });
});
