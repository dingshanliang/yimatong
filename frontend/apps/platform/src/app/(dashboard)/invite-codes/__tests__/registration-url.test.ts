import { describe, expect, it } from "vitest";

import { registrationUrl } from "../registration-url";

describe("registrationUrl", () => {
  it("uses the canonical URL returned by the backend without guessing an origin", () => {
    const backendUrl =
      "https://brand-admin.example.com/register?invite_code=SERVER-CODE";

    expect(registrationUrl({ registration_url: backendUrl })).toBe(backendUrl);
  });
});
