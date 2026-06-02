import type { Metadata } from "next";
import Script from "next/script";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { AdminThemeProvider } from "@/lib/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "一码通管理后台",
  description: "一码通 — 包装扫码增长 SaaS 管理后台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full" suppressHydrationWarning>
      <head>
        {process.env.NODE_ENV === "development" && (
          <Script
            src="/react-grab.js"
            strategy="afterInteractive"
          />
        )}
      </head>
      <body className="h-full min-h-screen">
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var m=localStorage.getItem("admin_theme_mode");if(m!=="light"&&m!=="dark"){m=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=m;document.documentElement.style.colorScheme=m}catch(e){document.documentElement.dataset.theme="light"}`,
          }}
        />
        <AntdRegistry>
          <AdminThemeProvider>{children}</AdminThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
