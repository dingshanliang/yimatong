import type { NextConfig } from "next";
import path from "node:path";
import withBundleAnalyzer from "@next/bundle-analyzer";

const connectSrc =
  process.env.NODE_ENV !== "production"
    ? "'self' http://localhost:* http://127.0.0.1:*"
    : "'self'";

const frameAncestors =
  process.env.H5_FRAME_ANCESTORS ||
  (process.env.NODE_ENV !== "production"
    ? "'self' http://localhost:3000 http://127.0.0.1:3000"
    : "'none'");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-XSS-Protection", value: "0" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      `script-src 'self'${process.env.NODE_ENV !== "production" ? " 'unsafe-inline' 'unsafe-eval'" : ""}`,
      `style-src 'self' 'unsafe-inline'${process.env.NODE_ENV !== "production" ? " https://fonts.googleapis.com" : ""}`,
      "img-src 'self' data: blob: https:",
      `font-src 'self'${process.env.NODE_ENV !== "production" ? " https://fonts.gstatic.com" : ""}`,
      `connect-src ${connectSrc}`,
      `frame-ancestors ${frameAncestors}`,
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
];

if (frameAncestors === "'none'") {
  securityHeaders.unshift({ key: "X-Frame-Options", value: "DENY" });
}

const nextConfig: NextConfig = {
  transpilePackages: ["@yimatong/design-tokens", "@yimatong/shared"],
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendUrl}/api/v1/:path*`,
      },
      // /c/:publicId 由 Next.js page.tsx SSR 处理
      // page.tsx 内部通过 API_BASE 直接 fetch 后端获取数据并渲染 React 组件
    ];
  },
};

export default withBundleAnalyzer({ enabled: process.env.ANALYZE === "true" })(
  nextConfig
);
