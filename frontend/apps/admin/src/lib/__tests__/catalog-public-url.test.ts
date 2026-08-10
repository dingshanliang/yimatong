import { describe, expect, it } from "vitest";

import { isCatalogPublicUrl } from "../catalog-public-url";

describe("catalog public URL contract", () => {
  it.each([
    "https://cdn.example.com/catalog/item.png",
    "/api/v1/files/public/tenant/product-image/a.png",
    "",
  ])("accepts consumer-safe catalog URL %s", (value) => {
    expect(isCatalogPublicUrl(value)).toBe(true);
  });

  it.each([
    "http://cdn.example.com/item.png",
    "https://localhost/item.png",
    "https://127.0.0.1/item.png",
    "https://192.168.1.5/item.png",
    "https://user:secret@example.com/item.png",
    "/api/v1/files/private/item.png",
    "/api/v1/files/public/a/../../../auth/logout",
    "/api/v1/files/public/a/%2e%2e/%2e%2e/auth/logout",
    "/api/v1/files/public/a\\..\\..\\auth\\logout",
  ])("rejects non-public catalog URL %s", (value) => {
    expect(isCatalogPublicUrl(value)).toBe(false);
  });
});
