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

/**
 * 扫码事件上报 hook
 *
 * 在组件 mount 时通过 sendBeacon 或 fetch 上报 view 事件。
 * 支持页面卸载时可靠上报（使用 sendBeacon 降级到 fetch keepalive）。
 */
export function useScanEvent({
  publicId,
  pageVersionId,
  scanToken,
  enabled = true,
}: UseScanEventOptions) {
  const reported = useRef(false);

  useEffect(() => {
    if (!enabled || !publicId || reported.current) return;

    reported.current = true;

    const payload = {
      event_type: "view",
      public_id: publicId,
      page_version_id: pageVersionId,
      timestamp: new Date().toISOString(),
    };

    // 尝试使用 sendBeacon（支持页面卸载时可靠上报）
    const reportWithBeacon = () => {
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        const blob = new Blob([JSON.stringify(payload)], {
          type: "application/json",
        });
        const url = `${apiClient.defaults.baseURL}/scan-events`;
        return navigator.sendBeacon(url, blob);
      }
      return false;
    };

    // 降级使用 fetch keepalive
    const reportWithFetch = async () => {
      try {
        await fetch(`${apiClient.defaults.baseURL}/scan-events`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(scanToken ? { Authorization: `Bearer ${scanToken}` } : {}),
          },
          body: JSON.stringify(payload),
          keepalive: true,
        });
      } catch {
        // 扫码事件上报失败不应阻断用户体验，静默处理
      }
    };

    // 优先 sendBeacon，降级 fetch
    if (!reportWithBeacon()) {
      reportWithFetch();
    }
  }, [enabled, publicId, pageVersionId, scanToken]);
}
