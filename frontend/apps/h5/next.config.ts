import type { NextConfig } from "next";
import path from "node:path";
import withBundleAnalyzer from "@next/bundle-analyzer";
import { buildConnectSrc } from "./src/lib/csp";

const connectSrc = buildConnectSrc(
  process.env.NODE_ENV,
  process.env.NEXT_PUBLIC_API_URL
);

function getAllowedDevOrigins() {
  const origins = new Set(["127.0.0.1"]);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (apiUrl) {
    try {
      origins.add(new URL(apiUrl).hostname);
    } catch {
      // Invalid API URLs are handled by the app; do not weaken the dev-origin allowlist.
    }
  }
  return Array.from(origins);
}

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
  allowedDevOrigins: getAllowedDevOrigins(),
  transpilePackages: ["@yimatong/design-tokens", "@yimatong/shared"],
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default withBundleAnalyzer({ enabled: process.env.ANALYZE === "true" })(
  nextConfig
);
