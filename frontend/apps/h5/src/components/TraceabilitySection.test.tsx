import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TraceabilitySection } from "./TraceabilitySection";

describe("TraceabilitySection authoritative batch facts", () => {
  it("prefers the production-batch origin over the product fallback", () => {
    const markup = renderToStaticMarkup(
      <TraceabilitySection
        codeData={{
          public_id: "PUBLIC-001",
          product: { origin: "产品默认产地" },
          batch: { origin: "本批次实际产地", batch_code: "PB-001" },
        }}
      />
    );

    expect(markup).toContain("本批次实际产地");
    expect(markup).not.toContain("产品默认产地");
  });

  it("does not substitute the product origin when the batch origin is missing", () => {
    const markup = renderToStaticMarkup(
      <TraceabilitySection
        codeData={{
          public_id: "PUBLIC-002",
          product: { origin: "产品默认产地" },
          batch: { origin: "", batch_code: "PB-002" },
        }}
      />
    );

    expect(markup).not.toContain("产品默认产地");
    expect(markup).toContain("未提供批次产地");
  });
});
