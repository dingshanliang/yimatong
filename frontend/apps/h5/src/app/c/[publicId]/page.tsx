import type { Metadata } from "next";
import { CodePageClient } from "./CodePageClient";

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
 * 浏览器同源请求后端 resolver，使 visitor 身份和可信代理链在解析、
 * scan token 与后续埋点之间保持一致。
 */
export default async function CodePage({ params }: CodePageProps) {
  const { publicId } = await params;
  return <CodePageClient publicId={publicId} />;
}
