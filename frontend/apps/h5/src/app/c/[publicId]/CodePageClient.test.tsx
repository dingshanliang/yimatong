import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { resolveContent } = vi.hoisted(() => ({
  resolveContent: vi.fn(({ jsonPayload }: { jsonPayload: unknown }) => (
    <pre data-testid="payload">{JSON.stringify(jsonPayload)}</pre>
  )),
}));

vi.mock("./ResolveContent", () => ({ ResolveContent: resolveContent }));

import { CodePageClient } from "./CodePageClient";

describe("CodePageClient", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    localStorage.clear();
    resolveContent.mockClear();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("reuses and refreshes the browser visitor identity on the same-origin resolver", async () => {
    localStorage.setItem("visitor_id", "visitor-before");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          code_data: { public_id: "CODE/ONE" },
          scan_info: { visitor_id: "visitor-after" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const root = createRoot(container);
    await act(async () => {
      root.render(
        <CodePageClient publicId="CODE/ONE" apiBase="https://api.example" />
      );
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example/c/CODE%2FONE",
      expect.objectContaining({
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "X-Visitor-ID": "visitor-before",
        },
      })
    );
    expect(localStorage.getItem("visitor_id")).toBe("visitor-after");
    expect(resolveContent).toHaveBeenCalledWith(
      expect.objectContaining({
        publicId: "CODE/ONE",
        jsonPayload: expect.objectContaining({
          scan_info: { visitor_id: "visitor-after" },
        }),
      }),
      undefined
    );

    await act(async () => root.unmount());
  });

  it("fails closed into the unavailable surface when resolver JSON cannot load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockRejectedValue(new Error("offline"))
    );
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <CodePageClient publicId="CODE-TWO" apiBase="https://api.example" />
      );
    });

    expect(resolveContent).toHaveBeenCalledWith(
      expect.objectContaining({ publicId: "CODE-TWO", jsonPayload: null }),
      undefined
    );
    await act(async () => root.unmount());
  });

  it("uses the member-bound OAuth credential once and removes it from the address bar", async () => {
    window.history.replaceState(
      null,
      "",
      "/c/CODE-OAUTH#oauth=success&scan_token=member-token&consent_id=consent-1"
    );
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({ scan_token: "anonymous-token", code_data: {} }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      )
    );
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <CodePageClient publicId="CODE-OAUTH" apiBase="https://api.example" />
      );
    });

    expect(resolveContent).toHaveBeenCalledWith(
      expect.objectContaining({
        jsonPayload: expect.objectContaining({ scan_token: "member-token" }),
      }),
      undefined
    );
    expect(window.location.hash).toBe("");
    expect(localStorage.getItem("consent_id:CODE-OAUTH")).toBe("consent-1");
    await act(async () => root.unmount());
  });

  it("never renders a non-success resolver JSON as a verified product", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ result: "not_found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <CodePageClient publicId="MISSING-CODE" apiBase="https://api.example" />
      );
    });

    expect(resolveContent).toHaveBeenCalledWith(
      expect.objectContaining({ publicId: "MISSING-CODE", jsonPayload: null }),
      undefined
    );
    await act(async () => root.unmount());
  });
});
