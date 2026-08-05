import axios from "axios";
import { describe, expect, it } from "vitest";

import { shouldRotateTenantOpeningKey } from "./tenant-opening-idempotency";

describe("tenant opening idempotency retry policy", () => {
  it("rotates after a definite 4xx rejection", () => {
    expect(
      shouldRotateTenantOpeningKey(
        new axios.AxiosError(
          "invalid",
          "ERR_BAD_REQUEST",
          undefined,
          undefined,
          {
            status: 422,
          } as never
        )
      )
    ).toBe(true);
  });

  it("retains the key for network failures and ambiguous 5xx responses", () => {
    expect(shouldRotateTenantOpeningKey(new axios.AxiosError("network"))).toBe(
      false
    );
    expect(
      shouldRotateTenantOpeningKey(
        new axios.AxiosError(
          "server",
          "ERR_BAD_RESPONSE",
          undefined,
          undefined,
          {
            status: 503,
          } as never
        )
      )
    ).toBe(false);
  });
});
