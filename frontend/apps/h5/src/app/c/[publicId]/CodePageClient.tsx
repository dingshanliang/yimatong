"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { ResolveContent } from "./ResolveContent";

/** 瞬时失败自动重试一次的退避间隔（可注入以便测试）。 */
const AUTO_RETRY_DELAY_MS = 800;

export function CodePageClient({
  publicId,
  apiBase = API_BASE,
  retryDelayMs = AUTO_RETRY_DELAY_MS,
}: {
  publicId: string;
  apiBase?: string;
  retryDelayMs?: number;
}) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let retriedOnce = false;
    const finish = () => {
      if (!controller.signal.aborted) {
        setLoaded(true);
        setRetrying(false);
      }
    };
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
        finish();
      } catch {
        if (controller.signal.aborted) return;
        // 瞬时故障自动重试一次（短暂退避）；仍失败才落到失败界面，
        // 失败界面提供人工"重新查验"（kc6d.3）。
        if (!retriedOnce) {
          retriedOnce = true;
          setTimeout(() => {
            if (!controller.signal.aborted) void load();
          }, retryDelayMs);
          return;
        }
        setPayload(null);
        finish();
      }
    };
    void load();
    return () => controller.abort();
  }, [apiBase, publicId, reloadKey, retryDelayMs]);

  const handleRetry = useCallback(() => {
    // 人工重试期间保持失败视图（按钮进入禁用加载态，防重复点击），
    // 新结果落地或再次失败后由 finish() 恢复。
    if (retrying) return;
    setRetrying(true);
    setPayload(null);
    setReloadKey((key) => key + 1);
  }, [retrying]);

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
      onRetry={handleRetry}
      retrying={retrying}
    />
  );
}
