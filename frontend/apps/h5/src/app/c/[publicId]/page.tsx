import type { Metadata } from "next";
import { SERVER_API_BASE } from "@/lib/api";
import { ResolveContent } from "./ResolveContent";

interface CodePageProps {
  params: Promise<{ publicId: string }>;
}

export async function generateMetadata({
  params,
}: CodePageProps): Promise<Metadata> {
  const { publicId } = await params;
  return {
    title: `产品溯源 - ${publicId}`,
    description: "扫码查看产品信息与溯源详情",
  };
}

/**
 * 码解析页 — 消费者扫码后看到的核心页面
 *
 * SSR 策略：
 * 1. 服务端 fetch 后端 /c/{public_id}
 * 2. 如果返回 JSON → 解析数据，渲染 React 组件
 * 3. 如果返回 HTML → Alpha 兼容模式，直接透传 HTML
 */
export default async function CodePage({ params }: CodePageProps) {
  const { publicId } = await params;

  let mode: "json" | "html" = "html";
  let jsonPayload: Record<string, unknown> | null = null;
  let htmlContent: string | null = null;

  try {
    const res = await fetch(`${SERVER_API_BASE}/c/${publicId}`, {
      headers: { Accept: "application/json, text/html" },
      cache: "no-store",
    });

    const contentType = res.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      jsonPayload = await res.json();
      mode = "json";
      // yimatong-zgb1.10 Decision 22：存 visitor_id 到 localStorage（first-party 稳定访客标识）
      const scanInfo = (jsonPayload as { scan_info?: { visitor_id?: string } })
        .scan_info;
      if (scanInfo?.visitor_id && typeof window !== "undefined") {
        try {
          localStorage.setItem("visitor_id", scanInfo.visitor_id);
        } catch {
          // localStorage 不可用时静默降级
        }
      }
    } else {
      // 后端当前返回 HTML — Alpha 兼容模式
      htmlContent = await res.text();
      mode = "html";
    }
  } catch {
    // 后端不可用时展示兜底 UI
    mode = "html";
    htmlContent = null;
  }

  return (
    <ResolveContent
      mode={mode}
      publicId={publicId}
      jsonPayload={jsonPayload}
      htmlContent={htmlContent}
    />
  );
}
