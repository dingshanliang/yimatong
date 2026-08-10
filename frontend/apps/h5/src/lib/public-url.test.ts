import { describe, expect, it } from "vitest";

import { safePublicUrl } from "./public-url";

describe("safePublicUrl", () => {
  it("accepts managed files and public HTTPS URLs", () => {
    expect(safePublicUrl("/api/v1/files/public/report.pdf")).toBe(
      "/api/v1/files/public/report.pdf"
    );
    expect(safePublicUrl(" https://assets.example.com/report.pdf ")).toBe(
      "https://assets.example.com/report.pdf"
    );
  });

  it.each([
    "javascript:alert(1)",
    "http://example.com/report.pdf",
    "https://user:pass@example.com/report.pdf",
    "https://localhost/report.pdf",
    "https://127.0.0.1/report.pdf",
    "https://10.0.0.1/report.pdf",
    "https://169.254.1.1/report.pdf",
    "/api/v1/files/public/a/../../../auth/logout",
    "/api/v1/files/public/a/%2e%2e/%2e%2e/auth/logout",
    "/api/v1/files/public/a\\..\\..\\auth\\logout",
  ])("rejects unsafe persisted URL %s", (value) => {
    expect(safePublicUrl(value)).toBeNull();
  });

  it("applies the same public-only contract to persisted brand logos", () => {
    expect(safePublicUrl("/api/v1/files/public/brand/logo.png")).toBe(
      "/api/v1/files/public/brand/logo.png"
    );
    expect(
      safePublicUrl("data:image/svg+xml,<svg onload=alert(1) />")
    ).toBeNull();
  });
});
