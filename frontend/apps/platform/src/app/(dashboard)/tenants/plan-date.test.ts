import { describe, expect, it } from "vitest";

import { planExpiryLabel, toChinaBusinessDate } from "./plan-date";

describe("China business plan date contract", () => {
  it("renders the backend UTC instant as the same Shanghai business date", () => {
    expect(toChinaBusinessDate("2027-07-01T15:59:59.999999Z")).toBe(
      "2027-07-01"
    );
    expect(planExpiryLabel("2027-07-01T15:59:59.999999Z")).toBe(
      "至 2027-07-01（北京时间当天 23:59:59）"
    );
  });

  it("labels a missing boundary as long-term validity", () => {
    expect(planExpiryLabel(null)).toBe("长期有效");
  });
});
