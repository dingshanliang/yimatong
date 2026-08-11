"use client";

import { useEffect, useRef } from "react";
import { apiClient } from "./api";

interface UseScanEventOptions {
  /** 码的公开 ID */
  publicId: string;
  /** 页面版本 ID（可选） */
  pageVersionId?: string;
  /** scan_token，用于鉴权扫码事件上报 */
  scanToken?: string;
  /** 是否启用（默认 true） */
  enabled?: boolean;
}

interface ScanEventPayload {
  event_type: "view";
  public_id: string;
  page_version_id?: string;
  timestamp: string;
  client_event_id: string;
}

interface ReportScanEventOptions {
  url: string;
  scanToken?: string;
  visitorId?: string;
  payload: ScanEventPayload;
  fetchImpl?: typeof fetch;
  maxAttempts?: number;
}

export async function reportScanEventWithRetry({
  url,
  scanToken,
  visitorId,
  payload,
  fetchImpl = fetch,
  maxAttempts = 2,
}: ReportScanEventOptions): Promise<boolean> {
  if (!scanToken) return false;

  const body = JSON.stringify(payload);
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${scanToken}`,
          ...(visitorId ? { "X-Visitor-ID": visitorId } : {}),
        },
        body,
        keepalive: true,
      });
      if (response.ok) return true;
    } catch {
      // A bounded retry uses the exact same client_event_id and request body.
    }
  }
  return false;
}

/**
 * 扫码事件上报 hook
 *
 * 在组件 mount 时通过带 Bearer 认证的 keepalive fetch 上报 view 事件。
 */
export function useScanEvent({
  publicId,
  pageVersionId,
  scanToken,
  enabled = true,
}: UseScanEventOptions) {
  const reported = useRef(false);
  const clientEventId = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !publicId || !scanToken || reported.current) return;

    reported.current = true;
    clientEventId.current ??= globalThis.crypto.randomUUID();

    const payload: ScanEventPayload = {
      event_type: "view",
      public_id: publicId,
      page_version_id: pageVersionId,
      timestamp: new Date().toISOString(),
      client_event_id: clientEventId.current,
    };

    // sendBeacon 无法附加 Authorization；scan_token 只放 Bearer header，
    // 不放 URL。有限重试复用同一个 client_event_id，由后端原子去重。
    void reportScanEventWithRetry({
      url: `${apiClient.defaults.baseURL}/scan-events`,
      scanToken,
      visitorId: localStorage.getItem("visitor_id") || undefined,
      payload,
    });
  }, [enabled, publicId, pageVersionId, scanToken]);
}
