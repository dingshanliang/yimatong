import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BrandHeader } from "./BrandHeader";

describe("BrandHeader public logo", () => {
  it("renders an approved public logo URL", () => {
    const markup = renderToStaticMarkup(
      <BrandHeader
        name="测试品牌"
        logoUrl="/api/v1/files/public/brand/logo.png"
      />
    );

    expect(markup).toContain('src="/api/v1/files/public/brand/logo.png"');
  });

  it.each([
    "javascript:alert(1)",
    "http://example.com/brand.png",
    "https://127.0.0.1/brand.png",
  ])("does not render an unsafe persisted logo URL %s", (logoUrl) => {
    const markup = renderToStaticMarkup(
      <BrandHeader name="测试品牌" logoUrl={logoUrl} />
    );

    expect(markup).not.toContain("<img");
    expect(markup).not.toContain(logoUrl);
  });

  it("fails closed for a historical non-string logo value", () => {
    const markup = renderToStaticMarkup(
      <BrandHeader
        name="测试品牌"
        logoUrl={
          { url: "https://assets.example.com/brand.png" } as unknown as string
        }
      />
    );

    expect(markup).not.toContain("<img");
  });
});
