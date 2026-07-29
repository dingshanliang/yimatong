import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";

export const metadata: Metadata = {
  title: "产品溯源",
  description: "扫码查看产品信息",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <head>
        {process.env.NODE_ENV === "development" && (
          <Script src="/react-grab.js" strategy="afterInteractive" />
        )}
      </head>
      <body className="min-h-screen bg-canvas text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
