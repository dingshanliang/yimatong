import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { PlatformThemeProvider } from "@/lib/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "一码通 · 平台管理",
  description: "一码通 SaaS 平台管理后台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full" suppressHydrationWarning>
      <body className="h-full min-h-screen">
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var m=localStorage.getItem("platform_theme_mode");if(m!=="light"&&m!=="dark"){m=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=m;document.documentElement.style.colorScheme=m}catch(e){document.documentElement.dataset.theme="light"}`,
          }}
        />
        <AntdRegistry>
          <PlatformThemeProvider>{children}</PlatformThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
