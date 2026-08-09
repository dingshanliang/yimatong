import { describe, expect, it } from "vitest";

import {
  buildPlanConfiguration,
  isValidQuotaValue,
  quotaLabel,
} from "../plan-config";

describe("Platform plan configuration", () => {
  it("accepts unlimited or non-negative integer quotas only", () => {
    expect(isValidQuotaValue(-1)).toBe(true);
    expect(isValidQuotaValue(0)).toBe(true);
    expect(isValidQuotaValue(10)).toBe(true);
    expect(isValidQuotaValue(undefined)).toBe(true);
    expect(isValidQuotaValue(-0.5)).toBe(false);
    expect(isValidQuotaValue(-2)).toBe(false);
    expect(isValidQuotaValue(1.5)).toBe(false);
  });

  it("keeps canonical product and cash-red-packet entitlements in payloads", () => {
    expect(
      buildPlanConfiguration({
        max_products: 20,
        max_codes_per_batch: -1,
        cash_red_packet: true,
      })
    ).toMatchObject({
      quota_defaults: { max_products: 20, max_codes_per_batch: -1 },
      feature_flags: { cash_red_packet: true },
    });
    expect(quotaLabel("max_products")).toBe("最大产品数");
  });
});
