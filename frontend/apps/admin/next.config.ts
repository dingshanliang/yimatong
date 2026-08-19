import type { NextConfig } from "next";
import path from "node:path";
import withBundleAnalyzer from "@next/bundle-analyzer";

function getApiConnectSources() {
  if (process.env.NODE_ENV === "production") return "";

  const sources = new Set([
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "ws://localhost:3000",
    "ws://127.0.0.1:3000",
  ]);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (apiUrl) {
    try {
      sources.add(new URL(apiUrl).origin);
    } catch {
      sources.add(apiUrl);
    }
  }
  return ` ${Array.from(sources).join(" ")}`;
}

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

const securityHeaders = [
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-XSS-Protection", value: "0" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // dev mode needs unsafe-inline + unsafe-eval for Next.js HMR and inline hydration scripts
      `script-src 'self'${process.env.NODE_ENV !== "production" ? " 'unsafe-inline' 'unsafe-eval'" : ""}`,
      `style-src 'self' 'unsafe-inline'${process.env.NODE_ENV !== "production" ? " https://fonts.googleapis.com" : ""}`,
      "img-src 'self' data: blob: https:",
      `font-src 'self'${process.env.NODE_ENV !== "production" ? " https://fonts.gstatic.com" : ""}`,
      `connect-src 'self'${getApiConnectSources()}${process.env.NODE_ENV !== "production" ? " https://www.react-grab.com" : ""}`,
      "frame-ancestors 'self'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
];

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
