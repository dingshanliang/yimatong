import { describe, expect, it, vi } from "vitest";

import { reportScanEventWithRetry } from "./useScanEvent";

const payload = {
  event_type: "view" as const,
  public_id: "public-code",
  timestamp: "2026-08-03T12:00:00.000Z",
  client_event_id: "7e642a75-e105-47ca-bcfd-18ed72885338",
};

describe("reportScanEventWithRetry", () => {
  it("reuses one idempotency payload and keeps the Bearer token out of the URL", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("transient"))
      .mockResolvedValueOnce(new Response(null, { status: 201 }));
    const token = "signed.scan.token";

    const reported = await reportScanEventWithRetry({
      url: "https://api.example.test/scan-events",
      scanToken: token,
      visitorId: "visitor-stable",
      payload,
      fetchImpl,
    });

    expect(reported).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchImpl.mock.calls) {
      expect(url).toBe("https://api.example.test/scan-events");
      expect(String(url)).not.toContain(token);
      expect(init?.headers).toMatchObject({
        Authorization: `Bearer ${token}`,
        "X-Visitor-ID": "visitor-stable",
      });
      expect(JSON.parse(String(init?.body))).toMatchObject({
        client_event_id: payload.client_event_id,
      });
    }
    expect(fetchImpl.mock.calls[0]?.[1]?.body).toBe(
      fetchImpl.mock.calls[1]?.[1]?.body
    );
  });

  it("fails closed without a scan token", async () => {
    const fetchImpl = vi.fn<typeof fetch>();

    const reported = await reportScanEventWithRetry({
      url: "https://api.example.test/scan-events",
      payload,
      fetchImpl,
    });

    expect(reported).toBe(false);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("retries a 503 with the exact same authenticated visitor event", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 201 }));

    expect(
      await reportScanEventWithRetry({
        url: "https://api.example.test/scan-events",
        scanToken: "signed.scan.token",
        visitorId: "visitor-stable",
        payload,
        fetchImpl,
      })
    ).toBe(true);

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]?.[1]).toEqual(fetchImpl.mock.calls[1]?.[1]);
  });
});
