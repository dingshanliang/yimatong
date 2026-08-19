import type { NextConfig } from "next";
import path from "node:path";

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

const nextConfig: NextConfig = {
  allowedDevOrigins: getAllowedDevOrigins(),
  transpilePackages: ["@yimatong/design-tokens", "@yimatong/shared"],
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
};

export default nextConfig;
