import { describe, expect, it } from "vitest";

import { refreshPreservesActingContext } from "../api";

function jwt(payload: Record<string, unknown>) {
  const encode = (value: Record<string, unknown>) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.signature`;
}

describe("refreshPreservesActingContext", () => {
  it("rejects replay when refresh drops the acting tenant", () => {
    expect(
      refreshPreservesActingContext(
        jwt({ acting_tenant_id: "client-1" }),
        jwt({ tenant_type: "agency" })
      )
    ).toBe(false);
  });

  it("allows replay when the same acting tenant is retained", () => {
    expect(
      refreshPreservesActingContext(
        jwt({ acting_tenant_id: "client-1" }),
        jwt({ acting_tenant_id: "client-1" })
      )
    ).toBe(true);
  });
});
