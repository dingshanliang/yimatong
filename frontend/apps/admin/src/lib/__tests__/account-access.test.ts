import { describe, expect, it } from "vitest";
import { canViewRoleDirectory } from "../account-access";

describe("account access policy", () => {
  it.each([
    ["admin", true],
    ["operator", true],
    ["viewer", false],
    [undefined, false],
  ])("allows the role directory for %s: %s", (role, expected) => {
    expect(canViewRoleDirectory(role)).toBe(expected);
  });
});
