import { describe, expect, it } from "vitest";

import { buildConnectSrc } from "./csp";

describe("H5 connect-src", () => {
  it("allows only the exact configured production API origin", () => {
    expect(buildConnectSrc("production", "https://api.example.cn")).toBe(
      "'self' https://api.example.cn"
    );
  });

  it.each([
    "http://api.example.cn",
    "https://user:secret@api.example.cn",
    "https://api.example.cn/api/v1",
    "not-an-absolute-url",
  ])("rejects an unsafe production API URL: %s", (value) => {
    expect(() => buildConnectSrc("production", value)).toThrow();
  });

  it.each([undefined, "", "   "])(
    "fails the production build without a public API origin: %s",
    (value) => {
      expect(() => buildConnectSrc("production", value)).toThrow(
        "NEXT_PUBLIC_API_URL is required in production"
      );
    }
  );

  it("keeps bounded localhost development origins", () => {
    expect(buildConnectSrc("development", "http://localhost:8000")).toBe(
      "'self' http://localhost:* http://127.0.0.1:* http://localhost:8000"
    );
  });
});
