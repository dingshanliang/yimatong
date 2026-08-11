"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { ResolveContent } from "./ResolveContent";

export function CodePageClient({
  publicId,
  apiBase = API_BASE,
}: {
  publicId: string;
  apiBase?: string;
}) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        if (!apiBase) throw new Error("Public API URL is not configured");
        const visitorId = localStorage.getItem("visitor_id");
        const response = await fetch(
          `${apiBase}/c/${encodeURIComponent(publicId)}`,
          {
            headers: {
              Accept: "application/json",
              ...(visitorId ? { "X-Visitor-ID": visitorId } : {}),
            },
            cache: "no-store",
            signal: controller.signal,
          }
        );
        if (!response.ok) throw new Error("Resolver rejected the public code");
        const data = (await response.json()) as Record<string, unknown>;
        const fragment = new URLSearchParams(window.location.hash.slice(1));
        const oauthScanToken = fragment.get("scan_token");
        const oauthConsentId = fragment.get("consent_id");
        if (fragment.get("oauth") === "success" && oauthScanToken) {
          data.scan_token = oauthScanToken;
          if (oauthConsentId) {
            localStorage.setItem(`consent_id:${publicId}`, oauthConsentId);
          }
          window.history.replaceState(null, "", window.location.pathname);
        }
        const scanInfo = data.scan_info as { visitor_id?: string } | undefined;
        if (scanInfo?.visitor_id)
          localStorage.setItem("visitor_id", scanInfo.visitor_id);
        setPayload(data);
      } catch {
        if (!controller.signal.aborted) setPayload(null);
      } finally {
        if (!controller.signal.aborted) setLoaded(true);
      }
    };
    void load();
    return () => controller.abort();
  }, [apiBase, publicId]);

  if (!loaded) {
    return (
      <div className="mx-auto max-w-md p-8 text-center">正在查验产品信息…</div>
    );
  }
  return (
    <ResolveContent
      mode="json"
      publicId={publicId}
      jsonPayload={payload}
      htmlContent={null}
    />
  );
}
