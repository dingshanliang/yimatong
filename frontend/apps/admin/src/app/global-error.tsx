"use client";

import { useEffect } from "react";
import "./globals.css";

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body>
        <main className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--admin-bg-layout)" }}>
          <div className="max-w-md rounded-lg p-6 text-center" style={{ background: "var(--admin-bg-container)", boxShadow: "var(--admin-shadow)" }}>
            <h1 className="mb-2 text-xl font-semibold">页面加载失败</h1>
            <p className="mb-4 text-sm text-text-muted">请重试加载页面；如果仍然失败，请刷新浏览器或重新登录。</p>
            <button
              className="rounded-md bg-[#1677ff] px-4 py-2 text-sm font-medium text-white"
              onClick={() => unstable_retry()}
              type="button"
            >
              重试
            </button>
          </div>
        </main>
      </body>
    </html>
  );
}
