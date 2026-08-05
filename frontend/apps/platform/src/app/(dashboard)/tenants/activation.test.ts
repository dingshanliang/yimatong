import { describe, expect, it } from "vitest";
import { canRetryInitialAdminActivation } from "./activation";

describe("canRetryInitialAdminActivation", () => {
  it("never exposes activation recovery for a terminated tenant", () => {
    expect(
      canRetryInitialAdminActivation({
        status: "terminated",
        activation_retryable: true,
      })
    ).toBe(false);
  });

  it("allows recovery only while a live tenant has pending activation", () => {
    expect(
      canRetryInitialAdminActivation({
        status: "active",
        activation_retryable: true,
      })
    ).toBe(true);
    expect(
      canRetryInitialAdminActivation({
        status: "suspended",
        activation_retryable: false,
      })
    ).toBe(false);
  });
});
