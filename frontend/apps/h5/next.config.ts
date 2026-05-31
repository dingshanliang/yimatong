import type { NextConfig } from "next";
import path from "node:path";
import withBundleAnalyzer from "@next/bundle-analyzer";

const frameAncestors = process.env.H5_FRAME_ANCESTORS ||
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
      "script-src 'self'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https:",
      "font-src 'self'",
      "connect-src 'self'",
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
  transpilePackages: ["@yimatong/shared"],
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
      {
        source: "/c/:publicId",
        destination: `${backendUrl}/c/:publicId`,
      },
    ];
  },
};

export default withBundleAnalyzer({ enabled: process.env.ANALYZE === "true" })(nextConfig);
