import type { NextConfig } from "next";
import withBundleAnalyzer from "@next/bundle-analyzer";

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-XSS-Protection", value: "0" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // dev mode needs unsafe-inline + unsafe-eval for Next.js HMR and inline hydration scripts
      `script-src 'self'${process.env.NODE_ENV !== "production" ? " 'unsafe-inline' 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https:",
      "font-src 'self'",
      `connect-src 'self'${process.env.NODE_ENV !== "production" ? " http://localhost:8000" : ""}`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  transpilePackages: ["@yimatong/shared"],
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default withBundleAnalyzer({ enabled: process.env.ANALYZE === "true" })(nextConfig);
