import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import theme from "@/lib/theme";
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
    <html lang="zh-CN" className="h-full">
      <body className="min-h-full">
        <AntdRegistry>
          <ConfigProvider theme={theme} locale={zhCN}>
            <App>{children}</App>
          </ConfigProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
